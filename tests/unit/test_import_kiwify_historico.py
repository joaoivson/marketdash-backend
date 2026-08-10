"""
Testes puros (sem banco) do script de import histórico Kiwify — parsing,
pares de continuação/upgrade e cálculo de estado por CPF.

Casos reais usados como fixture (import_assinaturas.csv / import_cobrancas.csv,
09/08/2026): Luiz (continuação), Cristiana (upgrade), Alexandre (CPFs
diferentes — 2 movimentos independentes), Lara (2x "Cancelado pelo produtor",
gap > 1 dia, sem cobrança correspondente).

Validado ao vivo contra homologação (Postgres real, banco vazio): as 5
faturamentos mensais (abr-ago) batem exatos com os números de aceite do Luiz
depois dos fixes de BRT/prioridade — este arquivo cobre a lógica pura que
antecede a gravação no banco.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from import_kiwify_historico import (  # noqa: E402
    _digits,
    _parse_brt,
    calcular_estado_por_cpf,
    calcular_pares_continuacao,
)

BRT = ZoneInfo("America/Sao_Paulo")


def _dt_brt(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=BRT).astimezone(timezone.utc)


def _linha_assinatura(**kwargs) -> dict:
    padrao = dict(
        nome="Fulana", email="fulana@example.com", cpf="00000000000",
        telefone=None, plano="Pro", periodicidade="monthly", liquido_cents=6050,
        status="active", inicio_brt=None, ultima_cobranca_brt=None,
        acesso_ate_brt=None, cancelada_em_brt=None, motivo_cancelamento=None,
        ja_no_painel=False,
    )
    padrao.update(kwargs)
    return padrao


def _linha_cobranca(**kwargs) -> dict:
    padrao = dict(
        order_ref="XYZ", nome="Fulana", email="fulana@example.com", cpf="00000000000",
        data_brt=None, plano="Pro", periodicidade="monthly",
        liquido_cents=6050, bruto_cents=6700, afiliado=None, comissao_afiliado_cents=0,
    )
    padrao.update(kwargs)
    return padrao


# --- parsing -----------------------------------------------------------------


def test_digits_remove_pontuacao():
    assert _digits("113.555.606-75") == "11355560675"
    assert _digits(None) == ""


def test_parse_brt_converte_para_utc():
    # BRT é UTC-3: 20:36 BRT = 23:36 UTC do mesmo dia.
    dt = _parse_brt("2026-04-07 20:36")
    assert dt == datetime(2026, 4, 7, 23, 36, tzinfo=timezone.utc)


def test_parse_brt_com_segundos():
    dt = _parse_brt("2026-04-14 11:23:34")
    assert dt == datetime(2026, 4, 14, 14, 23, 34, tzinfo=timezone.utc)


def test_parse_brt_vazio_e_none():
    assert _parse_brt("") is None
    assert _parse_brt(None) is None


def test_parse_brt_boundary_vira_dia_seguinte_em_utc():
    """Caso real (breno): 21:09 BRT de 31/05 é 00:09 UTC de 01/06 — a razão de
    todo bucketing por mês no admin_metrics_service precisar usar BRT."""
    dt = _parse_brt("2026-05-31 21:09:47")
    assert dt == datetime(2026, 6, 1, 0, 9, 47, tzinfo=timezone.utc)


# --- calcular_pares_continuacao -----------------------------------------------


def test_caso_luiz_continuacao_mesmo_instante():
    cpf = "11355560675"
    linhas = [
        _linha_assinatura(cpf=cpf, plano="Pro", periodicidade="monthly",
                           inicio_brt=_dt_brt("2026-04-14 11:23"),
                           cancelada_em_brt=_dt_brt("2026-06-15 10:50")),
        _linha_assinatura(cpf=cpf, plano="Pro", periodicidade="monthly",
                           inicio_brt=_dt_brt("2026-06-15 10:50"), status="active"),
    ]
    cobrancas = [_linha_cobranca(order_ref="C1", cpf=cpf, data_brt=_dt_brt("2026-06-15 10:50"))]
    pares, cobrancas_marcadas = calcular_pares_continuacao(linhas, cobrancas)
    assert pares[(cpf, linhas[0]["inicio_brt"].isoformat())] == "continuacao"
    assert pares[(cpf, linhas[1]["inicio_brt"].isoformat())] == "continuacao"
    assert "C1" in cobrancas_marcadas


def test_caso_cristiana_upgrade_plano_diferente_9_dias():
    cpf = "2253612995"
    linhas = [
        _linha_assinatura(cpf=cpf, plano="Pro", periodicidade="yearly",
                           inicio_brt=_dt_brt("2026-05-18 16:15"),
                           cancelada_em_brt=_dt_brt("2026-05-22 18:40")),
        _linha_assinatura(cpf=cpf, plano="Pro", periodicidade="quarterly",
                           inicio_brt=_dt_brt("2026-05-31 18:43"), status="active"),
    ]
    cobrancas = [_linha_cobranca(order_ref="C2", cpf=cpf, data_brt=_dt_brt("2026-05-31 18:43"))]
    pares, cobrancas_marcadas = calcular_pares_continuacao(linhas, cobrancas)
    assert pares[(cpf, linhas[0]["inicio_brt"].isoformat())] == "upgrade"
    assert pares[(cpf, linhas[1]["inicio_brt"].isoformat())] == "upgrade"
    assert "C2" in cobrancas_marcadas


def test_caso_alexandre_cpfs_diferentes_nao_forma_par():
    """Mesmo e-mail, CPFs diferentes entre as 2 assinaturas — são 2 movimentos
    independentes (1 churn + 1 nova), não um par de continuação/upgrade."""
    linhas = [
        _linha_assinatura(cpf="10677893671", email="alex@example.com", plano="Pro",
                           periodicidade="monthly", inicio_brt=_dt_brt("2026-05-05 01:05"),
                           cancelada_em_brt=_dt_brt("2026-07-02 05:50")),
        _linha_assinatura(cpf="11068644680", email="alex@example.com", plano="Pro",
                           periodicidade="monthly", inicio_brt=_dt_brt("2026-07-09 14:17"),
                           status="active"),
    ]
    pares, cobrancas_marcadas = calcular_pares_continuacao(linhas, [])
    assert pares == {}
    assert cobrancas_marcadas == set()


def test_caso_lara_gap_maior_que_1_dia_mesmo_plano_nao_forma_par():
    """~41h de gap, mesmo plano: não é continuação (>1 dia) nem upgrade (mesmo
    plano) — 2 movimentos genuinamente distintos, sem cobrança correspondente."""
    cpf = "14193503666"
    linhas = [
        _linha_assinatura(cpf=cpf, plano="Pro", periodicidade="monthly",
                           inicio_brt=_dt_brt("2026-04-10 09:14"),
                           cancelada_em_brt=_dt_brt("2026-04-10 18:35")),
        _linha_assinatura(cpf=cpf, plano="Pro", periodicidade="monthly",
                           inicio_brt=_dt_brt("2026-04-12 12:07"),
                           cancelada_em_brt=_dt_brt("2026-04-14 11:32")),
    ]
    pares, cobrancas_marcadas = calcular_pares_continuacao(linhas, [])
    assert pares == {}
    assert cobrancas_marcadas == set()


def test_gap_negativo_nao_quebra_e_nao_marca(caplog):
    """Nova assinatura começando ANTES do cancelamento anterior — anomalia,
    não deve marcar nada nem lançar exceção."""
    cpf = "999"
    linhas = [
        _linha_assinatura(cpf=cpf, inicio_brt=_dt_brt("2026-05-01 00:00"),
                           cancelada_em_brt=_dt_brt("2026-05-10 00:00")),
        _linha_assinatura(cpf=cpf, inicio_brt=_dt_brt("2026-05-05 00:00"), status="active"),
    ]
    pares, cobrancas_marcadas = calcular_pares_continuacao(linhas, [])
    assert pares == {}


# --- calcular_estado_por_cpf --------------------------------------------------


def test_estado_usa_a_assinatura_mais_recente_por_cpf():
    cpf = "123"
    linhas = [
        _linha_assinatura(cpf=cpf, status="canceled",
                           inicio_brt=_dt_brt("2026-04-01 00:00"),
                           acesso_ate_brt=_dt_brt("2026-05-01 00:00"),
                           cancelada_em_brt=_dt_brt("2026-04-05 00:00"),
                           motivo_cancelamento="Pagamento não efetuado"),
        _linha_assinatura(cpf=cpf, status="active",
                           inicio_brt=_dt_brt("2026-06-01 00:00"),
                           acesso_ate_brt=_dt_brt("2026-07-01 00:00")),
    ]
    estado = calcular_estado_por_cpf(linhas)
    assert estado[cpf]["subscription_status"] == "active"
    assert estado[cpf]["has_access"] is True
    assert estado[cpf]["cancel_reason"] is None


def test_estado_cancelado_carrega_motivo_e_data():
    cpf = "456"
    linhas = [
        _linha_assinatura(cpf=cpf, status="canceled",
                           inicio_brt=_dt_brt("2026-07-10 19:10"),
                           acesso_ate_brt=_dt_brt("2026-10-10 19:12"),
                           cancelada_em_brt=_dt_brt("2026-07-21 07:15"),
                           motivo_cancelamento="Cancelado pelo comprador no app do banco"),
    ]
    estado = calcular_estado_por_cpf(linhas)
    assert estado[cpf]["subscription_status"] == "canceled"
    assert estado[cpf]["has_access"] is True  # acesso_ate_brt no futuro relativo à data do teste
    assert estado[cpf]["cancel_reason"] == "Cancelado pelo comprador no app do banco"
    assert estado[cpf]["canceled_at"] == _dt_brt("2026-07-21 07:15")
