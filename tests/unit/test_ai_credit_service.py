"""
Créditos do Diagnóstico IA.

Saldo é derivado do ledger do mês corrente. Reset é implícito: virou o mês,
a soma recomeça — não existe job de reset pra falhar.
"""
from datetime import datetime, timezone

import pytest

from app.services.ai_credit_service import (
    CUSTO_CHAT, CUSTO_GERACAO, AiCreditService, SaldoInsuficiente,
)


class _FakeLedgerRepo:
    def __init__(self, gasto_no_mes=0):
        self._gasto = gasto_no_mes
        self.gravados = []

    def total_gasto_no_mes(self, user_id, inicio_do_mes):
        return self._gasto

    def registrar(self, user_id, diagnostic_id, tipo, creditos, saldo_apos):
        self.gravados.append(
            {"user_id": user_id, "diagnostic_id": diagnostic_id, "tipo": tipo,
             "creditos": creditos, "saldo_apos": saldo_apos}
        )


def _servico(gasto=0):
    return AiCreditService(repo=_FakeLedgerRepo(gasto))


def test_cota_por_plano():
    s = _servico()
    assert s.cota("essencial") == 0
    assert s.cota("pro") == 200
    assert s.cota("max") == 1000


def test_plano_desconhecido_cai_no_minimo():
    assert _servico().cota("plano_inventado") == 0


def test_saldo_e_cota_menos_gasto_do_mes():
    assert _servico(gasto=30).saldo(1, "pro") == 170


def test_saldo_nunca_fica_negativo():
    assert _servico(gasto=500).saldo(1, "pro") == 0


def test_essencial_nao_tem_saldo():
    s = _servico()
    assert s.saldo(1, "essencial") == 0
    assert s.tem_saldo(1, "essencial", CUSTO_GERACAO) is False


def test_tem_saldo_na_fronteira_exata():
    s = _servico(gasto=190)   # sobram 10, custo da geração é 10
    assert s.tem_saldo(1, "pro", CUSTO_GERACAO) is True
    s2 = _servico(gasto=191)
    assert s2.tem_saldo(1, "pro", CUSTO_GERACAO) is False


def test_debitar_grava_no_extrato_e_devolve_saldo():
    repo = _FakeLedgerRepo(gasto_no_mes=0)
    s = AiCreditService(repo=repo)
    restante = s.debitar(1, "pro", "geracao", CUSTO_GERACAO, diagnostic_id=7)
    assert restante == 190
    assert repo.gravados == [
        {"user_id": 1, "diagnostic_id": 7, "tipo": "geracao",
         "creditos": 10, "saldo_apos": 190}
    ]


def test_debitar_sem_saldo_levanta_e_nao_grava():
    repo = _FakeLedgerRepo(gasto_no_mes=195)
    s = AiCreditService(repo=repo)
    with pytest.raises(SaldoInsuficiente) as exc:
        s.debitar(1, "pro", "geracao", CUSTO_GERACAO)
    assert exc.value.saldo == 5
    assert exc.value.necessario == 10
    assert repo.gravados == []


def test_custos_conforme_spec():
    assert (CUSTO_GERACAO, CUSTO_CHAT) == (10, 1)
