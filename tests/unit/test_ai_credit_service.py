"""
Créditos do Diagnóstico IA.

Saldo é derivado do ledger do mês corrente. Reset é implícito: virou o mês,
a soma recomeça — não existe job de reset pra falhar.
"""
from datetime import datetime, timezone

import pytest

from app.services.ai_credit_service import (
    CUSTO_CHAT, CUSTO_GERACAO, AiCreditService, SaldoInsuficiente, _inicio_do_mes,
)


class _FakeLedgerRepo:
    """Fake simples: implementa debitar_atomico compondo total_gasto_no_mes +
    registrar, sem exigir que a chamada aconteça sob lock. Usado pelos testes
    que não são sobre a corrida em si (saldo, cota, débito feliz/infeliz)."""

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

    def debitar_atomico(self, user_id, inicio_do_mes, cota, creditos, tipo,
                         diagnostic_id=None):
        gasto = self.total_gasto_no_mes(user_id, inicio_do_mes)
        saldo_atual = max(cota - gasto, 0)
        if saldo_atual < creditos:
            return saldo_atual, False
        restante = saldo_atual - creditos
        self.registrar(user_id, diagnostic_id, tipo, creditos, restante)
        return restante, True


class _FakeLedgerRepoComLock:
    """Fake que SIMULA o advisory lock: só permite ler o gasto do mês ou
    gravar o débito enquanto o "lock" estiver aberto (dentro de
    debitar_atomico), e registra a ORDEM das operações.

    Se o service voltar a fazer check-then-act (chamar total_gasto_no_mes e
    registrar por fora de debitar_atomico, como no bug original), essas
    chamadas acontecem com o lock fechado e o fake explode — é exatamente
    a corrida que a correção elimina.
    """

    def __init__(self, gasto_no_mes=0):
        self._gasto = gasto_no_mes
        self.gravados = []
        self.ordem = []
        self._lock_aberto = False

    def total_gasto_no_mes(self, user_id, inicio_do_mes):
        if not self._lock_aberto:
            raise AssertionError(
                "saldo somado fora do escopo do lock — corrida possível"
            )
        self.ordem.append("soma_saldo")
        return self._gasto

    def registrar(self, user_id, diagnostic_id, tipo, creditos, saldo_apos):
        if not self._lock_aberto:
            raise AssertionError(
                "débito gravado fora do escopo do lock — corrida possível"
            )
        self.ordem.append("grava_debito")
        self.gravados.append(
            {"user_id": user_id, "diagnostic_id": diagnostic_id, "tipo": tipo,
             "creditos": creditos, "saldo_apos": saldo_apos}
        )

    def debitar_atomico(self, user_id, inicio_do_mes, cota, creditos, tipo,
                         diagnostic_id=None):
        self.ordem.append("adquire_lock")
        self._lock_aberto = True
        try:
            gasto = self.total_gasto_no_mes(user_id, inicio_do_mes)
            saldo_atual = max(cota - gasto, 0)
            if saldo_atual < creditos:
                return saldo_atual, False
            restante = saldo_atual - creditos
            self.registrar(user_id, diagnostic_id, tipo, creditos, restante)
            return restante, True
        finally:
            self._lock_aberto = False


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


# --- Defeito 1: débito precisa ser atômico (lock antes da soma) ---------

def test_debitar_usa_caminho_atomico_lock_antes_da_soma_e_do_insert():
    """debitar() delega no repository um único passo atômico: a soma do gasto
    e a gravação do débito só podem acontecer com o lock aberto, e nessa
    ordem. Se o service voltasse a somar/gravar por fora (check-then-act),
    o fake abaixo levantaria AssertionError antes mesmo de chegarmos aqui."""
    repo = _FakeLedgerRepoComLock(gasto_no_mes=0)
    s = AiCreditService(repo=repo)

    restante = s.debitar(1, "pro", "geracao", CUSTO_GERACAO, diagnostic_id=7)

    assert restante == 190
    assert repo.ordem == ["adquire_lock", "soma_saldo", "grava_debito"]
    assert repo.gravados == [
        {"user_id": 1, "diagnostic_id": 7, "tipo": "geracao",
         "creditos": 10, "saldo_apos": 190}
    ]


def test_debitar_sem_saldo_no_caminho_atomico_nao_grava():
    """Saldo insuficiente: a soma acontece sob lock, mas o insert nunca roda —
    prova que a verificação e o corte de saldo insuficiente também vivem
    dentro do escopo atômico, não depois dele."""
    repo = _FakeLedgerRepoComLock(gasto_no_mes=195)
    s = AiCreditService(repo=repo)

    with pytest.raises(SaldoInsuficiente) as exc:
        s.debitar(1, "pro", "geracao", CUSTO_GERACAO)

    assert exc.value.saldo == 5
    assert exc.value.necessario == 10
    assert repo.ordem == ["adquire_lock", "soma_saldo"]  # sem "grava_debito"
    assert repo.gravados == []


# --- Defeito 2: corte do mês em América/São_Paulo, não em UTC -----------

def test_inicio_do_mes_usa_calendario_de_brasilia_nao_utc(monkeypatch):
    """21h59 do dia 31/07 em Brasília já é 00h59 de 01/08 em UTC. Se o corte
    usasse UTC (bug original), _inicio_do_mes() devolveria 01/08 00:00 UTC —
    resetando o saldo da aluna ~3h antes da hora, ainda em julho pra ela."""
    fixed_now_utc = datetime(2026, 8, 1, 1, 0, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now_utc if tz is None else fixed_now_utc.astimezone(tz)

    monkeypatch.setattr("app.services.ai_credit_service.datetime", _FixedDateTime)

    inicio = _inicio_do_mes()

    # Correto: início de JULHO (mês corrente em Brasília), convertido pra UTC.
    assert inicio == datetime(2026, 7, 1, 3, 0, 0, tzinfo=timezone.utc)
    # Documenta o valor que o bug em UTC produziria, pra deixar claro o que
    # este teste está travando.
    bugado_em_utc = fixed_now_utc.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    assert inicio != bugado_em_utc
