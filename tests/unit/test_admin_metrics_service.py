"""Regressão: dedupe de cobrança duplicada (item 1) e prioridade de evento pro
estado atual da assinatura (item 3 — next_payment não pode vir de webhook velho).
Task 2: status atrasado, late ≠ churn, late ≠ revenue."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.admin_metrics_service import (
    CANCEL_EVENTS,
    PAID_EVENTS,
    _client_display_status,
    _dedupe_by_charge,
    _is_active_now,
    _latest_by_subscriber,
    _paid_total_for_events,
    revenue_from_charges_for_month,
)


def _ev(**kwargs):
    defaults = dict(
        event_type="order_approved",
        order_id=None,
        subscription_id="sub-1",
        customer_email="user@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        next_payment=None,
        subscription_status=None,
        has_access=None,
        access_until=None,
        amount_net_cents=None,
        charges_completed=None,
        card_rejection_reason=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_dedupe_by_charge_collapses_same_order_id():
    a = _ev(event_type="order_approved", order_id="c4456ec2")
    b = _ev(event_type="subscription_renewed", order_id="c4456ec2")
    result = _dedupe_by_charge([a, b])
    assert result == [a]  # primeiro visto vence, segundo é descartado


def test_dedupe_by_charge_keeps_distinct_orders():
    a = _ev(order_id="order-1")
    b = _ev(order_id="order-2")
    result = _dedupe_by_charge([a, b])
    assert result == [a, b]


def test_dedupe_by_charge_never_collapses_events_without_order_id():
    a = _ev(order_id=None)
    b = _ev(order_id=None)
    result = _dedupe_by_charge([a, b])
    assert result == [a, b]


def test_latest_by_subscriber_prefers_subscription_renewed_over_later_order_approved():
    """Caso real (Letícia, 25/07/2026): order_approved chegou ~83ms DEPOIS de
    subscription_renewed pro mesmo subscription_id, mas com next_payment
    desatualizado (do dia da renovação, não do próximo ciclo). O evento mais
    'recente' por received_at não pode vencer aqui — subscription_renewed é
    quem sabe o next_payment certo."""
    now = datetime(2026, 7, 25, 6, 15, 55, tzinfo=timezone.utc)
    renewed = _ev(
        event_type="subscription_renewed",
        next_payment=datetime(2026, 8, 25, tzinfo=timezone.utc),  # correto
        received_at=now,
    )
    approved = _ev(
        event_type="order_approved",
        next_payment=datetime(2026, 7, 25, 8, 55, tzinfo=timezone.utc),  # desatualizado
        received_at=now + timedelta(milliseconds=83),  # chegou DEPOIS
    )
    latest = _latest_by_subscriber([renewed, approved])
    assert len(latest) == 1
    chosen = next(iter(latest.values()))
    assert chosen is renewed
    assert chosen.next_payment == datetime(2026, 8, 25, tzinfo=timezone.utc)


def test_latest_by_subscriber_still_advances_on_genuinely_newer_renewal():
    """Garantir que a prioridade não trava o estado no primeiro subscription_renewed
    pra sempre — uma renovação seguinte (mais tarde) deve vencer normalmente."""
    first_renewal = _ev(
        event_type="subscription_renewed",
        next_payment=datetime(2026, 8, 25, tzinfo=timezone.utc),
        received_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    second_renewal = _ev(
        event_type="subscription_renewed",
        next_payment=datetime(2026, 9, 25, tzinfo=timezone.utc),
        received_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )
    latest = _latest_by_subscriber([first_renewal, second_renewal])
    chosen = next(iter(latest.values()))
    assert chosen is second_renewal


def test_latest_by_subscriber_cancellation_after_renewal_wins():
    renewal = _ev(
        event_type="subscription_renewed",
        received_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    cancel = _ev(
        event_type="subscription_canceled",
        received_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    latest = _latest_by_subscriber([renewal, cancel])
    chosen = next(iter(latest.values()))
    assert chosen is cancel


def test_latest_by_subscriber_late_beats_later_order_approved():
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    late = _ev(event_type="subscription_late", received_at=now)
    approved = _ev(
        event_type="order_approved",
        received_at=now + timedelta(milliseconds=50),
    )
    latest = _latest_by_subscriber([late, approved])
    assert next(iter(latest.values())) is late


def test_late_with_expired_access_is_atrasado_not_active():
    today = date(2026, 7, 28)
    ev = _ev(
        event_type="subscription_late",
        subscription_status="waiting_payment",
        has_access=True,
        access_until=datetime(2026, 7, 20, tzinfo=timezone.utc),
        card_rejection_reason="refused_bank",
    )
    assert _is_active_now(ev, today) is False
    assert _client_display_status(ev, is_active=False) == "atrasado"


def test_late_with_valid_access_is_atrasado_still_active_eligible():
    today = date(2026, 7, 28)
    ev = _ev(
        event_type="subscription_late",
        subscription_status="waiting_payment",
        has_access=True,
        access_until=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert _is_active_now(ev, today) is True
    assert _client_display_status(ev, is_active=True) == "atrasado"


def test_waiting_payment_status_alone_is_atrasado():
    ev = _ev(event_type="order_approved", subscription_status="waiting_payment")
    assert _client_display_status(ev, is_active=False) == "atrasado"


def test_late_does_not_count_as_churn():
    assert CANCEL_EVENTS == {"subscription_canceled"}
    assert "subscription_late" not in CANCEL_EVENTS
    late = _ev(event_type="subscription_late", subscription_status="waiting_payment")
    assert _client_display_status(late, is_active=False) == "atrasado"
    assert _client_display_status(late, is_active=False) != "inativo"


def test_canceled_without_access_is_inativo_churn():
    ev = _ev(event_type="subscription_canceled", subscription_status="canceled")
    assert _client_display_status(ev, is_active=False) == "inativo"


def test_canceled_with_access_is_cancelado_com_acesso():
    ev = _ev(event_type="subscription_canceled", subscription_status="canceled")
    assert _client_display_status(ev, is_active=True) == "cancelado_com_acesso"


def test_subscription_late_not_in_revenue():
    """Late sem cobrança paid não altera faturamento."""
    late = _ev(
        event_type="subscription_late",
        order_id="late-1",
        amount_net_cents=9999,
        charges_completed=[
            {
                "order_id": "w1",
                "status": "waiting_payment",
                "Commissions": {"my_commission": 9999, "charge_amount": 10000},
            }
        ],
    )
    assert "subscription_late" not in PAID_EVENTS
    assert _paid_total_for_events([late]) == 0
    assert revenue_from_charges_for_month([late], 2026, 7)["net"] == 0
