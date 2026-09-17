"""Drill-down dos cards do painel: quem compõe MRR, Faturamento e Churn.

O admin via um total e não tinha como conferir de onde ele vinha. Clicar no card
agora abre a lista de clientes filtrada por aquela população.

A regra que mais importa aqui: **plano anual ou trimestral pontua INTEIRO no mês
em que foi pago**, não rateado. É o oposto do MRR, que divide o anual por 12 —
um é caixa, o outro é receita recorrente.
"""

from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import (
    AdminMetricsService,
    bounds_do_periodo,
)


def _ev(**kw):
    base = dict(
        event_type="order_approved",
        order_id="ord-1",
        subscription_id="sub-1",
        customer_email="a@b.com",
        customer_cpf=None,
        customer_name="Fulana",
        received_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc),
        amount_net_cents=65100,
        amount_gross_cents=70000,
        charges_completed=None,
        is_plan_change=False,
        refunded_at=None,
        next_payment=None,
        subscription_status=None,
        has_access=None,
        access_until=None,
        plan_name="Pro",
        plan_id=None,
        plan_frequency="annually",
        card_rejection_reason=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def _svc():
    return AdminMetricsService(MagicMock())


# --------------------------------------------------------------------------- #
#  A regra do anual/trimestral                                                 #
# --------------------------------------------------------------------------- #


def test_anual_pontua_inteiro_no_mes_do_pagamento():
    """R$ 651 de plano anual pago em setembro conta R$ 651 em setembro —
    não R$ 54,25 (que seria o rateio por 12)."""
    anual = _ev(plan_frequency="annually", amount_net_cents=65100,
                received_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    entra, valor = _svc()._pertence_ao_card(
        "faturamento", [anual], "sub-1", None, *bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert entra is True
    assert valor == 65100


def test_anual_NAO_aparece_nos_outros_meses_do_ano():
    """Se fosse rateado, ele apareceria nos 12 meses. Pagou em setembro, conta
    em setembro — outubro não tem nada dele."""
    anual = _ev(received_at=datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc))
    entra, valor = _svc()._pertence_ao_card(
        "faturamento", [anual], "sub-1", None, *bounds_do_periodo(date(2026, 10, 1), date(2026, 10, 31))
    )
    assert entra is False
    assert valor == 0


def test_trimestral_segue_a_mesma_regra():
    tri = _ev(plan_frequency="quarterly", amount_net_cents=18000,
              received_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
    entra, valor = _svc()._pertence_ao_card(
        "faturamento", [tri], "sub-1", None, *bounds_do_periodo(date(2026, 8, 1), date(2026, 8, 31))
    )
    assert (entra, valor) == (True, 18000)


def test_periodo_total_soma_as_cobrancas_de_todos_os_meses():
    evs = [
        _ev(order_id="o1", received_at=datetime(2026, 7, 5, tzinfo=timezone.utc), amount_net_cents=1000),
        _ev(order_id="o2", received_at=datetime(2026, 8, 5, tzinfo=timezone.utc), amount_net_cents=2000),
        _ev(order_id="o3", received_at=datetime(2026, 9, 5, tzinfo=timezone.utc), amount_net_cents=3000),
    ]
    entra, valor = _svc()._pertence_ao_card(
        "faturamento", evs, "sub-1", None, *bounds_do_periodo(None, None)
    )
    assert (entra, valor) == (True, 6000)


# --------------------------------------------------------------------------- #
#  Churn e MRR                                                                 #
# --------------------------------------------------------------------------- #


def test_churn_pega_quem_cancelou_no_periodo():
    evs = [
        _ev(),
        _ev(event_type="subscription_canceled", order_id="c1",
            received_at=datetime(2026, 9, 20, tzinfo=timezone.utc)),
    ]
    entra, _ = _svc()._pertence_ao_card(
        "churn", evs, "sub-1", None, *bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert entra is True


def test_churn_ignora_cancelamento_que_e_troca_de_plano():
    """Upgrade cancela a assinatura antiga com is_plan_change=True. Isso não é
    saída de cliente — e o card de churn também não conta."""
    evs = [
        _ev(),
        _ev(event_type="subscription_canceled", order_id="c1", is_plan_change=True,
            received_at=datetime(2026, 9, 20, tzinfo=timezone.utc)),
    ]
    entra, _ = _svc()._pertence_ao_card(
        "churn", evs, "sub-1", None, *bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert entra is False


def test_churn_de_outro_mes_nao_entra():
    evs = [_ev(event_type="subscription_canceled", order_id="c1",
               received_at=datetime(2026, 7, 3, tzinfo=timezone.utc))]
    entra, _ = _svc()._pertence_ao_card(
        "churn", evs, "sub-1", None, *bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert entra is False


def test_mrr_usa_a_base_de_quem_renova():
    entra, _ = _svc()._pertence_ao_card("mrr", [], "sub-1", {"sub-1"}, None, None)
    assert entra is True
    fora, _ = _svc()._pertence_ao_card("mrr", [], "sub-9", {"sub-1"}, None, None)
    assert fora is False


def test_sem_origem_nao_filtra_nada():
    entra, valor = _svc()._pertence_ao_card("", [], "sub-1", None, None, None)
    assert (entra, valor) == (True, None)
