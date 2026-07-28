"""Testes do DRE gerencial — alinhamento com faturamento líquido."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_dre_service import AdminDreService


def _ev(**kwargs):
    defaults = dict(
        event_type="order_approved",
        order_id=None,  # None = não sofre dedupe por cobrança (ver _dedupe_by_charge)
        amount_gross_cents=10000,
        amount_net_cents=9000,
        fee_cents=1000,
        refunded_at=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        charges_completed=None,
        subscription_id="sub-1",
        customer_email="a@example.com",
        customer_cpf=None,
        id=1,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _expense(category="Infra", amount_cents=2000, d=date(2026, 7, 5)):
    return SimpleNamespace(category=category, amount_cents=amount_cents, date=d)


def _mock_db(all_events, refund, expenses):
    """month_statement: (1) all events + order_by, (2) refunds filter, (3) expenses."""
    db = MagicMock()
    call = {"n": 0}

    def query(model):
        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        call["n"] += 1
        if call["n"] == 1:
            q.all.return_value = all_events
        elif call["n"] == 2:
            q.all.return_value = refund
        else:
            q.all.return_value = expenses
        return q

    db.query.side_effect = query
    return db


def test_dre_net_matches_paid_minus_refund_and_expenses():
    paid = [
        _ev(amount_gross_cents=10000, amount_net_cents=9000, fee_cents=1000, id=1),
        _ev(amount_gross_cents=5000, amount_net_cents=4500, fee_cents=500, id=2),
    ]
    refund = [
        _ev(
            event_type="order_refunded",
            amount_gross_cents=5000,
            amount_net_cents=4500,
            fee_cents=500,
            refunded_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            id=3,
        )
    ]
    expenses = [_expense("Infra", 1500), _expense("Ferramentas", 500)]
    db = _mock_db(paid, refund, expenses)

    stmt = AdminDreService(db).month_statement(2026, 7)
    assert stmt["gross_cents"] == 15000
    assert stmt["refund_gross_cents"] == 5000
    assert stmt["gross_after_refund_cents"] == 10000
    assert stmt["fees_cents"] == 1000  # 1500 - 500
    assert stmt["revenue_net_cents"] == 9000  # 13500 - 4500
    assert stmt["expenses_total_cents"] == 2000
    assert stmt["result_cents"] == 7000
    assert stmt["has_expenses"] is True


def test_dre_does_not_double_count_same_charge_across_webhook_types():
    """Regressão: Kiwify manda order_approved E subscription_renewed pra MESMA
    cobrança (mesmo order_id, mesmo valor) — sem dedupe por order_id, o faturamento
    dobrava (caso real: renovação da Letícia, R$60,50 líq contado como R$121)."""
    paid = [
        _ev(event_type="order_approved", order_id="c4456ec2", amount_gross_cents=6700, amount_net_cents=6050, id=1),
        _ev(event_type="subscription_renewed", order_id="c4456ec2", amount_gross_cents=6700, amount_net_cents=6050, id=2),
    ]
    db = _mock_db(paid, [], [])

    stmt = AdminDreService(db).month_statement(2026, 7)
    assert stmt["gross_cents"] == 6700  # não 13400
    assert stmt["revenue_net_cents"] == 6050  # não 12100


def test_dre_revenue_uses_charges_completed_by_charge_date():
    """Cobrança de abril só vista num webhook de julho entra no DRE de abril."""
    charge = {
        "order_id": "bruna1",
        "status": "paid",
        "approved_date": "2026-04-28T12:00:00Z",
        "Commissions": {"my_commission": 13570, "charge_amount": 14700},
    }
    events = [
        _ev(
            event_type="order_approved",
            order_id="bruna1",
            amount_gross_cents=14700,
            amount_net_cents=13570,
            fee_cents=1130,
            received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
            charges_completed=[charge],
            id=1,
        )
    ]
    db = _mock_db(events, [], [])

    apr = AdminDreService(db).month_statement(2026, 4)
    assert apr["revenue_net_cents"] == 13570
    assert apr["gross_cents"] == 14700

    # Reinicia mock — month_statement faz 3 queries de novo
    db = _mock_db(events, [], [])
    jul = AdminDreService(db).month_statement(2026, 7)
    assert jul["revenue_net_cents"] == 0
    assert jul["gross_cents"] == 0
