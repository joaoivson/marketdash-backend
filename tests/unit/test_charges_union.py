from datetime import datetime, timezone
from types import SimpleNamespace
from app.services.admin_metrics_service import (
    extract_paid_charges_union,
    total_paid_net_from_charges,
    revenue_from_charges_for_month,
)

def _ev(charges):
    return SimpleNamespace(charges_completed=charges, subscription_id="sub1")

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
