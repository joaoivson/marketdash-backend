"""Testes do DRE gerencial — alinhamento com faturamento líquido."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_dre_service import AdminDreService


def _ev(**kwargs):
    defaults = dict(
        event_type="order_approved",
        amount_gross_cents=10000,
        amount_net_cents=9000,
        fee_cents=1000,
        refunded_at=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _expense(category="Infra", amount_cents=2000, d=date(2026, 7, 5)):
    return SimpleNamespace(category=category, amount_cents=amount_cents, date=d)


def test_dre_net_matches_paid_minus_refund_and_expenses():
    paid = [
        _ev(amount_gross_cents=10000, amount_net_cents=9000, fee_cents=1000),
        _ev(amount_gross_cents=5000, amount_net_cents=4500, fee_cents=500),
    ]
    refund = [
        _ev(
            event_type="order_refunded",
            amount_gross_cents=5000,
            amount_net_cents=4500,
            fee_cents=500,
            refunded_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        )
    ]
    expenses = [_expense("Infra", 1500), _expense("Ferramentas", 500)]

    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "SubscriptionEvent" in name or model.__name__ == "SubscriptionEvent":
            # first call paid, second refunds — track via filter chain
            q.filter.return_value = q
            q.all.side_effect = [paid, refund]
        else:
            q.filter.return_value = q
            q.all.return_value = expenses
        return q

    # AdminDreService queries SubscriptionEvent twice then Expense once
    call = {"n": 0}

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        call["n"] += 1
        if call["n"] == 1:
            q.all.return_value = paid
        elif call["n"] == 2:
            q.all.return_value = refund
        else:
            q.all.return_value = expenses
        return q

    db.query.side_effect = query

    stmt = AdminDreService(db).month_statement(2026, 7)
    assert stmt["gross_cents"] == 15000
    assert stmt["refund_gross_cents"] == 5000
    assert stmt["gross_after_refund_cents"] == 10000
    assert stmt["fees_cents"] == 1000  # 1500 - 500
    assert stmt["revenue_net_cents"] == 9000  # 13500 - 4500
    assert stmt["expenses_total_cents"] == 2000
    assert stmt["result_cents"] == 7000
    assert stmt["has_expenses"] is True
