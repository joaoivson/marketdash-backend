"""DRE gerencial — calculado na hora a partir de subscription_events + expenses."""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timezone
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.expense import Expense
from app.models.subscription_event import SubscriptionEvent
from app.services.admin_metrics_service import PAID_EVENTS, REFUND_EVENTS, _month_bounds


class AdminDreService:
    def __init__(self, db: Session):
        self.db = db

    def month_statement(self, year: int, month: int) -> Dict[str, Any]:
        start, end = _month_bounds(year, month)
        paid = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(PAID_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
            )
            .all()
        )
        refunds = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(REFUND_EVENTS),
                func.coalesce(SubscriptionEvent.refunded_at, SubscriptionEvent.received_at) >= start,
                func.coalesce(SubscriptionEvent.refunded_at, SubscriptionEvent.received_at) <= end,
            )
            .all()
        )
        expenses = (
            self.db.query(Expense)
            .filter(Expense.date >= start.date(), Expense.date <= end.date())
            .all()
        )

        gross = sum((e.amount_gross_cents or 0) for e in paid)
        net = sum((e.amount_net_cents or 0) for e in paid)
        fees = sum((e.fee_cents or 0) for e in paid)
        refund_gross = sum((e.amount_gross_cents or 0) for e in refunds)
        refund_net = sum((e.amount_net_cents or 0) for e in refunds)
        refund_fees = sum((e.fee_cents or 0) for e in refunds)

        gross_after_refund = gross - refund_gross
        fees_after = max(fees - refund_fees, 0)
        # Receita líquida caixa = net pagos − net estornos (bate com card faturamento)
        revenue_net = net - refund_net

        by_cat: Dict[str, int] = {}
        for ex in expenses:
            by_cat[ex.category] = by_cat.get(ex.category, 0) + (ex.amount_cents or 0)
        expenses_total = sum(by_cat.values())
        result = revenue_net - expenses_total
        margin = round(result / revenue_net, 4) if revenue_net else None

        return {
            "year": year,
            "month": month,
            "gross_cents": gross,
            "refund_gross_cents": refund_gross,
            "gross_after_refund_cents": gross_after_refund,
            "fees_cents": fees_after,
            "revenue_net_cents": revenue_net,
            "expenses_by_category": [{"category": k, "amount_cents": v} for k, v in sorted(by_cat.items())],
            "expenses_total_cents": expenses_total,
            "result_cents": result,
            "margin": margin,
            "has_expenses": expenses_total > 0 or len(expenses) > 0,
        }

    def series_12m(self) -> List[Dict[str, Any]]:
        today = datetime.now(timezone.utc).date()
        out = []
        results = []
        for i in range(11, -1, -1):
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            stmt = self.month_statement(y, m)
            out.append({
                "month": f"{y:04d}-{m:02d}",
                "revenue_net_cents": stmt["revenue_net_cents"],
                "expenses_total_cents": stmt["expenses_total_cents"],
                "result_cents": stmt["result_cents"],
            })
            if stmt["has_expenses"] or stmt["revenue_net_cents"]:
                results.append(stmt["result_cents"])
        burn = None
        if len(results) >= 3:
            last3 = results[-3:]
            avg = sum(last3) / 3
            if avg < 0:
                burn = int(round(abs(avg)))
        # MoM
        mom = None
        if len(out) >= 2:
            cur = out[-1]["result_cents"]
            prev = out[-2]["result_cents"]
            delta = cur - prev
            pct = round(delta / abs(prev), 4) if prev else None
            mom = {"delta_cents": delta, "delta_pct": pct}
        return {"months": out, "burn_avg_3m_cents": burn, "mom": mom}

    def full(self, year: int, month: int) -> Dict[str, Any]:
        stmt = self.month_statement(year, month)
        series = self.series_12m()
        return {**stmt, "series": series["months"], "burn_avg_3m_cents": series["burn_avg_3m_cents"], "mom": series["mom"]}
