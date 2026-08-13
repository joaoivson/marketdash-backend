"""Rodada 7, item 2: denominador do churn passa a ser quem estava
RENOVANDO no início do mês, não toda a base com acesso (que inclui
cancelado-com-acesso — gente que não é mais receita recorrente esperada,
mas segue "ativa" pro produto)."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


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
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=None,
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


def test_churn_denominador_usa_renovando_nao_toda_a_base_de_acesso():
    svc = AdminMetricsService(MagicMock())

    renovando = [_ev(id=i, subscription_id=f"sub-{i}") for i in range(1, 21)]  # 20 renovando
    cancelada_com_acesso = [
        _ev(
            id=100 + i,
            subscription_id=f"sub-canc-{i}",
            event_type="subscription_canceled",
            subscription_status="canceled",
        )
        for i in range(17)
    ]  # +17 com acesso mas já cancelada = 37 na base de acesso total

    svc.active_subscribers = lambda as_of=None: renovando + cancelada_com_acesso
    svc.renewing_subscribers = lambda as_of=None: renovando

    svc.db.query.return_value.filter.return_value.all.return_value = []  # nenhum cancelamento no mês
    svc._agora = lambda: datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)

    resultado = svc.churn_for_month(2026, 8)

    assert resultado["start_actives"] == 20  # não 37
