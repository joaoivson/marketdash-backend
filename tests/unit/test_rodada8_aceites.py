"""Rodada 8 — aceites do Luiz, com a base descrita no relatório de 15/08.

Itens 2 e 4. O item 1 (denominador do churn) está em
test_churn_denominador_renovando.py e o item 3 é execução manual.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.core.plans import list_price_cents
from app.services.admin_metrics_service import AdminMetricsService, _latest_by_subscriber


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        subscription_id="sub-1",
        customer_email="a@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        approved_date=datetime(2026, 7, 10, tzinfo=timezone.utc),
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=None,
        next_payment=None,
        amount_net_cents=6000,
        amount_gross_cents=6700,
        fee_cents=None,
        refunded_at=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# --------------------------------------------------------------- item 2 --

class TestPrecoDeTabelaDoMax:
    """O Max caía no preço do Pro (`max → pro`), tirando R$30/mês do bruto."""

    @pytest.mark.parametrize(
        "freq,esperado",
        [("mensal", 9700), ("monthly", 9700),
         ("trimestral", 20700), ("quarterly", 20700),
         ("anual", 62700), ("yearly", 62700)],
    )
    def test_max_tem_preco_proprio(self, freq, esperado):
        assert list_price_cents("max", freq) == esperado

    def test_max_nao_e_mais_precificado_como_pro(self):
        assert list_price_cents("max", "mensal") != list_price_cents("pro", "mensal")

    def test_planos_antigos_seguem_iguais(self):
        assert list_price_cents("pro", "mensal") == 6700
        assert list_price_cents("pro", "trimestral") == 14700
        assert list_price_cents("pro", "anual") == 44700
        assert list_price_cents("essencial", "mensal") == 4700


def test_bruto_do_mrr_bate_com_a_tabela_do_luiz():
    """Base de 15/08: 12 Pro mensal + 8 Essencial + 10 Pro trimestral +
    2 Pro anual + 1 Max mensal = R$ 1.841,50 de bruto mensalizado.

    Mensalização: 147÷3 = 49,00 e 447÷12 = 37,25 — precisão cheia por
    assinante, arredonda só no total.
    """
    eventos = []
    i = 0

    def add(qtd, plano, freq, gross):
        nonlocal i
        for _ in range(qtd):
            i += 1
            eventos.append(_ev(
                id=i, subscription_id=f"sub-{i}", customer_email=f"a{i}@ex.com",
                plan_name=plano, plan_id=plano.lower(), plan_frequency=freq,
                amount_gross_cents=gross, amount_net_cents=gross,
            ))

    add(12, "Pro", "monthly", 6700)
    add(8, "Essencial", "monthly", 4700)
    add(10, "Pro", "quarterly", 14700)
    add(2, "Pro", "yearly", 44700)
    add(1, "Max", "monthly", 9700)

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: eventos
    svc._last_paid_for = lambda ev: ev

    assert len(svc.renewing_subscribers()) == 33
    assert svc.mrr_cents()["gross"] == 184150


def test_import_e_webhook_da_mesma_assinatura_contam_uma_vez():
    """Caso real: cancelou em 15/08 e seguiu somando R$49/mês no MRR.

    O import histórico não trouxe `subscription_id` (chave `cpf:`) e o
    cancelamento trouxe (chave `sub:`) — o cancelamento não alcançava a linha do
    import, que ficava congelada em "ativo".
    """
    email = "carine@ex.com"
    do_import = _ev(
        id=1, subscription_id=None, customer_cpf="6616678474", customer_email=email,
        event_type="import_subscription_active",
        received_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        approved_date=datetime(2026, 5, 23, tzinfo=timezone.utc),
        plan_frequency="quarterly", amount_gross_cents=14700, amount_net_cents=14700,
        access_until=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )
    cancelamento = _ev(
        id=2, subscription_id="sub-ccfe494c", customer_cpf=None, customer_email=email,
        event_type="subscription_canceled", subscription_status="canceled",
        received_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        canceled_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        access_until=datetime(2026, 8, 23, tzinfo=timezone.utc),
    )

    latest = _latest_by_subscriber([do_import, cancelamento])
    assert len(latest) == 1, "import e webhook da mesma assinatura viraram 2 assinantes"

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [do_import, cancelamento]
    svc._last_paid_for = lambda ev: ev
    assert svc.renewing_subscribers() == [], "cancelada continua contando como renovando"
    assert svc.mrr_cents()["gross"] == 0


def test_upgrade_continua_com_duas_chaves_separadas():
    """Rede de proteção: duas chaves `sub:` da mesma pessoa são upgrade —
    a antiga fica cancelada, a nova ativa, e NÃO podem ser fundidas."""
    email = "luiz@ex.com"
    antiga = _ev(
        id=1, subscription_id="sub-antiga", customer_email=email,
        event_type="subscription_canceled", subscription_status="canceled",
        received_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        canceled_at=datetime(2026, 8, 15, tzinfo=timezone.utc),
        is_plan_change=True,
    )
    nova = _ev(
        id=2, subscription_id="sub-nova", customer_email=email,
        plan_name="Max", plan_id="max", amount_gross_cents=9700,
        received_at=datetime(2026, 8, 15, 0, 1, tzinfo=timezone.utc),
        approved_date=datetime(2026, 8, 15, 0, 1, tzinfo=timezone.utc),
    )

    latest = _latest_by_subscriber([antiga, nova])
    assert len(latest) == 2

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [antiga, nova]
    svc._last_paid_for = lambda ev: ev
    # só a nova é receita recorrente; a antiga está cancelada
    assert svc.mrr_cents()["gross"] == 9700


# --------------------------------------------------------------- item 4 --

def test_ponto_do_mes_corrente_e_o_mesmo_numero_do_card(monkeypatch):
    """Dois MRR na mesma tela confundem. Enquanto o mês não fecha, o último
    ponto da série É o card."""
    fixed_now = datetime(2026, 8, 20, 12, 0, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("app.services.admin_metrics_service.datetime", _FixedDateTime)

    db = MagicMock()
    min_q = MagicMock()
    min_q.scalar.return_value = datetime(2026, 8, 1, tzinfo=timezone.utc)
    all_q = MagicMock()
    all_q.all.return_value = []
    db.query.side_effect = [min_q, all_q]

    svc = AdminMetricsService(db)
    monkeypatch.setattr(svc, "revenue_for_month", lambda y, m: {"net": 0, "gross": 0, "refund_net": 0})
    monkeypatch.setattr(svc, "new_vs_canceled_series", lambda: [])
    # o card
    monkeypatch.setattr(svc, "mrr_cents", lambda actives=None: {"net": 164745, "gross": 184150})
    # o método do histórico devolve OUTRO valor de propósito: se o ponto do mês
    # corrente sair daqui, o teste falha — que era exatamente o bug.
    monkeypatch.setattr(
        svc, "mrr_at", lambda momento, periodos=None, cancelamentos=None: {"net": 173100, "gross": 999999}
    )

    series = svc.series_12m()

    assert series["mrr"][-1]["month"] == "2026-08"
    assert series["mrr"][-1]["net"] == 164745, "ponto do mês corrente ≠ card de MRR"
    assert series["mrr"][-1]["gross"] == 184150
