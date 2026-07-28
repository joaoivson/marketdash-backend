from types import SimpleNamespace

from app.services.admin_metrics_service import (
    _paid_total_for_events,
    extract_paid_charges_union,
    revenue_from_charges_for_month,
    total_paid_net_from_charges,
)


def _ev(charges, **kwargs):
    defaults = dict(
        charges_completed=charges,
        subscription_id="sub1",
        event_type="order_approved",
        order_id=None,
        amount_net_cents=0,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_union_dedupes_same_order_across_webhooks():
    c1 = {"order_id": "o1", "status": "paid", "approved_date": "2026-05-26",
          "Commissions": {"my_commission": 6050, "charge_amount": 6700}}
    c2 = {"order_id": "o2", "status": "paid", "approved_date": "2026-06-25",
          "Commissions": {"my_commission": 6050, "charge_amount": 6700}}
    c3 = {"order_id": "o3", "status": "paid", "approved_date": "2026-07-25",
          "Commissions": {"my_commission": 6050, "charge_amount": 6700}}
    # webhook A traz 2; webhook B traz as 3 (histórico completo)
    events = [_ev([c1, c2]), _ev([c1, c2, c3])]
    union = extract_paid_charges_union(events)
    assert len(union) == 3
    assert total_paid_net_from_charges(events) == 18150


def test_union_reads_flat_amount_field():
    ch = {
        "order_id": "bruna1",
        "status": "paid",
        "created_at": "2026-04-28T09:10:48.195Z",
        "amount": 13570,
    }
    events = [SimpleNamespace(charges_completed=[ch], subscription_id="s")]
    assert total_paid_net_from_charges(events) == 13570
    rev = revenue_from_charges_for_month(events, 2026, 4)
    assert rev["net"] == 13570
    assert rev["gross"] == 13570


def test_skips_non_paid():
    events = [_ev([{"order_id": "x", "status": "waiting_payment",
                    "Commissions": {"my_commission": 999}}])]
    assert total_paid_net_from_charges(events) == 0


def test_revenue_month_uses_charge_date_not_webhook_received():
    # cobrança de abril vista só num webhook de julho → entra em 2026-04
    ch = {"order_id": "bruna1", "status": "paid", "approved_date": "2026-04-28T12:00:00Z",
          "Commissions": {"my_commission": 13570, "charge_amount": 14700}}
    events = [_ev([ch])]
    rev = revenue_from_charges_for_month(events, 2026, 4)
    assert rev["net"] == 13570
    assert revenue_from_charges_for_month(events, 2026, 7)["net"] == 0


def test_paid_total_prefers_union_when_paid_charges_exist():
    paid_ch = {
        "order_id": "o1",
        "status": "paid",
        "approved_date": "2026-05-26",
        "Commissions": {"my_commission": 6050, "charge_amount": 6700},
    }
    # Legacy amount would be wrong / different — union must win
    events = [
        _ev([paid_ch], event_type="order_approved", order_id="o1", amount_net_cents=9999),
    ]
    assert _paid_total_for_events(events) == 6050


def test_paid_total_falls_back_when_charges_completed_missing():
    events = [
        _ev(None, event_type="order_approved", order_id="legacy-1", amount_net_cents=4500),
        _ev(None, event_type="subscription_renewed", order_id="legacy-1", amount_net_cents=4500),
    ]
    assert _paid_total_for_events(events) == 4500


def test_union_fallback_raw_payload_when_charges_completed_none():
    charges = [
        {"order_id": "o1", "status": "paid", "approved_date": "2026-05-26", "amount": 6050},
        {"order_id": "o2", "status": "paid", "approved_date": "2026-06-25", "amount": 6050},
        {"order_id": "o3", "status": "paid", "approved_date": "2026-07-25", "amount": 6050},
    ]
    events = [
        SimpleNamespace(
            charges_completed=None,
            raw_payload={"Subscription": {"charges": {"completed": charges}}},
            subscription_id="sub1",
        )
    ]
    assert total_paid_net_from_charges(events) == 18150


def test_paid_total_falls_back_when_only_non_paid_charges():
    waiting = {
        "order_id": "w1",
        "status": "waiting_payment",
        "Commissions": {"my_commission": 999},
    }
    events = [
        _ev([waiting], event_type="order_approved", order_id="legacy-2", amount_net_cents=7200),
    ]
    # Truthy array must NOT zero totals — fallback to PAID_EVENTS dedupe
    assert extract_paid_charges_union(events) == []
    assert _paid_total_for_events(events) == 7200
