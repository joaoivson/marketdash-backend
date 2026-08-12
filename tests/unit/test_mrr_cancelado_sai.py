"""Rodada 6, item 2: cancelada sai do MRR no mês do cancelamento.

O acesso continua (produto), a receita esperada não (negócio). Caso Daniel:
cancelou em julho com acesso até outubro — churn em julho e MRR até outubro
era incoerente.
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import (
    AdminMetricsService,
    _is_canceled,
    cancel_instants,
)


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        subscription_id="sub-1",
        customer_email="daniel@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=datetime(2026, 10, 10, tzinfo=timezone.utc),
        next_payment=None,
        amount_net_cents=None,
        amount_gross_cents=None,
        fee_cents=None,
        approved_date=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_is_canceled_reconhece_evento_e_status():
    assert _is_canceled(_ev(event_type="subscription_canceled")) is True
    assert _is_canceled(_ev(subscription_status="canceled")) is True
    assert _is_canceled(_ev()) is False


def test_cancel_instants_ignora_plan_change_e_ajuste_do_produtor():
    real = _ev(
        id=1,
        event_type="subscription_canceled",
        canceled_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    upgrade = _ev(id=2, event_type="subscription_canceled", is_plan_change=True)
    produtor = _ev(
        id=3, event_type="subscription_canceled", cancel_reason="Cancelado pelo produtor"
    )
    instantes = cancel_instants([real, upgrade, produtor])
    todos = [dt for lista in instantes.values() for dt in lista]
    assert todos == [datetime(2026, 7, 20, tzinfo=timezone.utc)]


def test_renewing_exclui_cancelada_com_acesso():
    svc = AdminMetricsService(MagicMock())
    cancelada = _ev(
        id=1,
        event_type="subscription_canceled",
        subscription_status="canceled",
        has_access=True,
        access_until=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    ativa = _ev(id=2, subscription_id="sub-2", customer_email="ana@example.com")
    svc._all_events = lambda: [cancelada, ativa]

    hoje = date(2026, 8, 11)
    com_acesso = svc.active_subscribers(as_of=hoje)
    renovando = svc.renewing_subscribers(as_of=hoje)

    assert len(com_acesso) == 2  # denominador do Uso mantém a cancelada
    assert [e.subscription_id for e in renovando] == ["sub-2"]


def test_mrr_at_zera_no_mes_do_cancelamento():
    svc = AdminMetricsService(MagicMock())
    periodos = {
        "sub:daniel": [
            {
                "inicio": datetime(2026, 7, 10, tzinfo=timezone.utc),
                "fim": datetime(2026, 10, 10, tzinfo=timezone.utc),
                "net_cents": 18150,
                "gross_cents": 19700,
                "divisor": 3,
            }
        ]
    }
    cancelamentos = {"sub:daniel": [datetime(2026, 7, 20, tzinfo=timezone.utc)]}

    antes = svc.mrr_at(datetime(2026, 7, 15, tzinfo=timezone.utc), periodos, cancelamentos)
    depois = svc.mrr_at(datetime(2026, 7, 31, tzinfo=timezone.utc), periodos, cancelamentos)
    agosto = svc.mrr_at(datetime(2026, 8, 31, tzinfo=timezone.utc), periodos, cancelamentos)

    assert antes["net"] == 6050  # 18150 / 3
    assert depois["net"] == 0
    assert agosto["net"] == 0


def test_cancelamento_anterior_ao_periodo_nao_derruba_reassinatura():
    """Cancelou em maio, voltou em julho: o cancelamento velho não mata o MRR novo."""
    svc = AdminMetricsService(MagicMock())
    periodos = {
        "cpf:1": [
            {
                "inicio": datetime(2026, 7, 1, tzinfo=timezone.utc),
                "fim": datetime(2026, 8, 1, tzinfo=timezone.utc),
                "net_cents": 6050,
                "gross_cents": 6700,
                "divisor": 1,
            }
        ]
    }
    cancelamentos = {"cpf:1": [datetime(2026, 5, 20, tzinfo=timezone.utc)]}
    assert svc.mrr_at(datetime(2026, 7, 20, tzinfo=timezone.utc), periodos, cancelamentos)["net"] == 6050
