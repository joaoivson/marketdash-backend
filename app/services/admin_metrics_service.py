"""Métricas do painel admin — calculadas na hora a partir de subscription_events."""
from __future__ import annotations

from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.models.facebook_integration import FacebookIntegration
from app.models.shopee_integration import ShopeeIntegration
from app.models.subscription_event import SubscriptionEvent
from app.models.user import User
from app.models.user_login import UserLogin
from app.models.ad_spend import AdSpend
from app.models.dataset_row import DatasetRow

PAID_EVENTS = {
    "order_approved",
    "subscription_renewed",
    "compra_aprovada",
}
REFUND_EVENTS = {
    "order_refunded",
    "order_chargedback",
    "chargeback",
    "compra_reembolsada",
}
CANCEL_EVENTS = {"subscription_canceled"}
FAILED_PAY_EVENTS = {
    "subscription_late",
    "order_refused",
    "compra_recusada",
}


def _month_bounds(year: int, month: int) -> Tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    last = monthrange(year, month)[1]
    end = datetime(year, month, last, 23, 59, 59, 999999, tzinfo=timezone.utc)
    return start, end


def _freq_divisor(frequency: Optional[str]) -> int:
    f = (frequency or "").lower()
    if f in ("quarterly", "trimestral", "quarter"):
        return 3
    if f in ("yearly", "annual", "anual", "year"):
        return 12
    return 1


def _normalize_plan_label(name: Optional[str], plan_id: Optional[str] = None) -> str:
    blob = f"{name or ''} {plan_id or ''}".lower()
    if "pro" in blob or "max" in blob:
        return "pro"
    if "essencial" in blob or "essential" in blob:
        return "essencial"
    return "essencial"


def _subscriber_key(ev: SubscriptionEvent) -> str:
    if ev.subscription_id:
        return f"sub:{ev.subscription_id}"
    if ev.customer_cpf:
        return f"cpf:{ev.customer_cpf}"
    if ev.customer_email:
        return f"email:{(ev.customer_email or '').lower()}"
    return f"id:{ev.id}"


def _latest_by_subscriber(events: List[SubscriptionEvent]) -> Dict[str, SubscriptionEvent]:
    latest: Dict[str, SubscriptionEvent] = {}
    for ev in sorted(events, key=lambda e: e.received_at or datetime.min.replace(tzinfo=timezone.utc)):
        latest[_subscriber_key(ev)] = ev
    return latest


def _is_active_now(ev: SubscriptionEvent, today: date) -> bool:
    if ev.has_access is False:
        return False
    if ev.has_access is True:
        if ev.access_until is None:
            return True
        return ev.access_until.date() >= today
    # fallback: subscription_status
    st = (ev.subscription_status or "").lower()
    if st in ("active", "ativa"):
        if ev.access_until and ev.access_until.date() < today:
            return False
        return True
    return False


class AdminMetricsService:
    def __init__(self, db: Session):
        self.db = db

    def _all_events(self) -> List[SubscriptionEvent]:
        return self.db.query(SubscriptionEvent).order_by(SubscriptionEvent.received_at.asc()).all()

    def active_subscribers(self, as_of: Optional[date] = None) -> List[SubscriptionEvent]:
        today = as_of or datetime.now(timezone.utc).date()
        latest = _latest_by_subscriber(self._all_events())
        return [ev for ev in latest.values() if _is_active_now(ev, today)]

    def mrr_cents(self, actives: Optional[List[SubscriptionEvent]] = None) -> Dict[str, int]:
        actives = actives if actives is not None else self.active_subscribers()
        net = 0
        gross = 0
        for ev in actives:
            div = _freq_divisor(ev.plan_frequency)
            # última cobrança paga da assinatura
            paid = self._last_paid_for(ev)
            n = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
            g = (paid.amount_gross_cents if paid else ev.amount_gross_cents) or 0
            net += n // div
            gross += g // div
        return {"net": net, "gross": gross}

    def _last_paid_for(self, ev: SubscriptionEvent) -> Optional[SubscriptionEvent]:
        q = self.db.query(SubscriptionEvent).filter(SubscriptionEvent.event_type.in_(PAID_EVENTS))
        if ev.subscription_id:
            q = q.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
        elif ev.customer_email:
            q = q.filter(SubscriptionEvent.customer_email == ev.customer_email)
        else:
            return None
        return q.order_by(SubscriptionEvent.received_at.desc()).first()

    def revenue_for_month(self, year: int, month: int) -> Dict[str, int]:
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
        gross = sum((e.amount_gross_cents or 0) for e in paid)
        net = sum((e.amount_net_cents or 0) for e in paid)
        refund_gross = sum((e.amount_gross_cents or 0) for e in refunds)
        refund_net = sum((e.amount_net_cents or 0) for e in refunds)
        return {
            "gross": gross - refund_gross,
            "net": net - refund_net,
            "refund_gross": refund_gross,
            "refund_net": refund_net,
        }

    def new_subscriptions(self, year: int, month: int) -> int:
        start, end = _month_bounds(year, month)
        paid = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(PAID_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.is_(False),
            )
            .all()
        )
        # primeiro pago da assinatura no período
        first_paid: Dict[str, datetime] = {}
        for ev in self.db.query(SubscriptionEvent).filter(SubscriptionEvent.event_type.in_(PAID_EVENTS)).all():
            key = _subscriber_key(ev)
            ts = ev.received_at or datetime.min.replace(tzinfo=timezone.utc)
            if key not in first_paid or ts < first_paid[key]:
                first_paid[key] = ts
        count = 0
        seen = set()
        for ev in paid:
            key = _subscriber_key(ev)
            if key in seen:
                continue
            fp = first_paid.get(key)
            if fp and start <= fp <= end:
                count += 1
                seen.add(key)
        return count

    def churn_for_month(self, year: int, month: int) -> Dict[str, Any]:
        start, end = _month_bounds(year, month)
        # ativos no início do mês
        start_actives = self.active_subscribers(as_of=(start - timedelta(seconds=1)).date())
        start_count = max(len(start_actives), 1)
        cancels = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(CANCEL_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.is_(False),
            )
            .all()
        )
        # unique by subscriber
        keys = {_subscriber_key(c) for c in cancels}
        n = len(keys)
        return {"count": n, "rate": round(n / start_count, 4), "start_actives": len(start_actives)}

    def renewal_rate(self, year: int, month: int) -> Optional[float]:
        start, end = _month_bounds(year, month)
        due = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.next_payment >= start,
                SubscriptionEvent.next_payment <= end,
            )
            .all()
        )
        # latest per sub with next_payment in month
        latest = _latest_by_subscriber(due)
        if not latest:
            return None
        renewed = 0
        for key, ev in latest.items():
            paid = (
                self.db.query(SubscriptionEvent)
                .filter(
                    SubscriptionEvent.event_type.in_(PAID_EVENTS),
                    SubscriptionEvent.received_at >= start,
                    SubscriptionEvent.received_at <= end,
                )
            )
            if ev.subscription_id:
                paid = paid.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
            elif ev.customer_email:
                paid = paid.filter(SubscriptionEvent.customer_email == ev.customer_email)
            if paid.first():
                renewed += 1
        return round(renewed / len(latest), 4)

    def ltv_estimate_cents(self, mrr_net: int, actives_count: int) -> Optional[int]:
        if actives_count <= 0 or mrr_net <= 0:
            return None
        # média móvel 3 meses de churn
        today = datetime.now(timezone.utc).date()
        rates = []
        for i in range(1, 4):
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            ch = self.churn_for_month(y, m)
            # só conta mês se houver eventos
            if self.db.query(SubscriptionEvent).filter(
                SubscriptionEvent.received_at >= _month_bounds(y, m)[0],
                SubscriptionEvent.received_at <= _month_bounds(y, m)[1],
            ).first():
                rates.append(ch["rate"])
        if len(rates) < 3:
            return None
        avg_churn = sum(rates) / len(rates)
        if avg_churn <= 0:
            return None
        arpu = mrr_net / actives_count
        return int(round(arpu / avg_churn))

    def plan_breakdown(self, actives: List[SubscriptionEvent]) -> Dict[str, int]:
        out = {"essencial": 0, "pro": 0, "max": 0}
        for ev in actives:
            label = _normalize_plan_label(ev.plan_name, ev.plan_id)
            out[label] = out.get(label, 0) + 1
        return out

    def alerts(self) -> Dict[str, int]:
        today = datetime.now(timezone.utc).date()
        actives = self.active_subscribers()
        soon = 0
        failed = 0
        never = 0
        no_login = 0
        for ev in actives:
            if ev.next_payment:
                d = ev.next_payment.date()
                if today <= d <= today + timedelta(days=7):
                    soon += 1
            # failed pay: latest event failed but still active
            latest_fail = None
            if ev.subscription_id or ev.customer_email:
                q = self.db.query(SubscriptionEvent).filter(SubscriptionEvent.event_type.in_(FAILED_PAY_EVENTS))
                if ev.subscription_id:
                    q = q.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
                else:
                    q = q.filter(SubscriptionEvent.customer_email == ev.customer_email)
                latest_fail = q.order_by(SubscriptionEvent.received_at.desc()).first()
            if latest_fail and latest_fail.received_at and (datetime.now(timezone.utc) - latest_fail.received_at).days <= 14:
                failed += 1

            uid = ev.user_id
            if not uid and ev.customer_email:
                u = self.db.query(User).filter(User.email == ev.customer_email).first()
                uid = u.id if u else None
            if uid:
                has_shopee = (
                    self.db.query(ShopeeIntegration)
                    .filter(ShopeeIntegration.user_id == uid, ShopeeIntegration.is_active.is_(True))
                    .first()
                )
                has_fb = (
                    self.db.query(FacebookIntegration)
                    .filter(FacebookIntegration.user_id == uid, FacebookIntegration.is_active.is_(True))
                    .first()
                )
                if not has_shopee and not has_fb:
                    never += 1
                last_login = (
                    self.db.query(UserLogin)
                    .filter(UserLogin.user_id == uid)
                    .order_by(UserLogin.logged_at.desc())
                    .first()
                )
                if not last_login or (today - last_login.logged_at.date()).days > 10:
                    no_login += 1
            else:
                never += 1
                no_login += 1
        return {
            "expiring_7d": soon,
            "payment_failed": failed,
            "never_connected": never,
            "no_login_10d": no_login,
        }

    def series_12m(self) -> Dict[str, List[Dict[str, Any]]]:
        today = datetime.now(timezone.utc).date()
        mrr_series = []
        rev_series = []
        for i in range(11, -1, -1):
            y = today.year
            m = today.month - i
            while m <= 0:
                m += 12
                y -= 1
            # MRR snapshot: approx using current logic as-of end of month (simplified: use revenue-based for past)
            rev = self.revenue_for_month(y, m)
            # For historical MRR we approximate with month-end actives if we had events; else 0
            end_day = monthrange(y, m)[1]
            actives = self.active_subscribers(as_of=date(y, m, end_day))
            mrr = self.mrr_cents(actives)
            label = f"{y:04d}-{m:02d}"
            mrr_series.append({"month": label, "net": mrr["net"], "gross": mrr["gross"]})
            rev_series.append({"month": label, "net": rev["net"], "gross": rev["gross"]})
        return {"mrr": mrr_series, "revenue": rev_series}

    def plan_frequency_distribution(self) -> List[Dict[str, Any]]:
        actives = self.active_subscribers()
        buckets: Dict[Tuple[str, str], Dict[str, int]] = defaultdict(lambda: {"count": 0, "revenue_net": 0})
        for ev in actives:
            plan = _normalize_plan_label(ev.plan_name, ev.plan_id)
            freq = (ev.plan_frequency or "monthly").lower()
            if freq in ("quarterly", "trimestral"):
                freq_label = "trimestral"
            elif freq in ("yearly", "annual", "anual"):
                freq_label = "anual"
            else:
                freq_label = "mensal"
            paid = self._last_paid_for(ev)
            net = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
            buckets[(plan, freq_label)]["count"] += 1
            buckets[(plan, freq_label)]["revenue_net"] += net
        total_rev = sum(v["revenue_net"] for v in buckets.values()) or 1
        out = []
        for (plan, freq), v in sorted(buckets.items()):
            out.append({
                "plan": plan,
                "frequency": freq,
                "count": v["count"],
                "revenue_net_cents": v["revenue_net"],
                "revenue_share": round(v["revenue_net"] / total_rev, 4),
            })
        return out

    def dashboard(self, year: int, month: int) -> Dict[str, Any]:
        actives = self.active_subscribers()
        mrr = self.mrr_cents(actives)
        rev = self.revenue_for_month(year, month)
        churn = self.churn_for_month(year, month)
        arpu = int(round(mrr["net"] / len(actives))) if actives else 0
        return {
            "year": year,
            "month": month,
            "mrr_net_cents": mrr["net"],
            "mrr_gross_cents": mrr["gross"],
            "revenue_net_cents": rev["net"],
            "revenue_gross_cents": rev["gross"],
            "refund_net_cents": rev["refund_net"],
            "active_count": len(actives),
            "active_by_plan": self.plan_breakdown(actives),
            "new_subscriptions": self.new_subscriptions(year, month),
            "churn_count": churn["count"],
            "churn_rate": churn["rate"],
            "renewal_rate": self.renewal_rate(year, month),
            "arpu_cents": arpu,
            "ltv_cents": self.ltv_estimate_cents(mrr["net"], len(actives)),
            "alerts": self.alerts(),
            "series": self.series_12m(),
            "plan_frequency": self.plan_frequency_distribution(),
        }

    def _semaphore(self, user_id: Optional[int]) -> str:
        if not user_id:
            return "red"
        today = datetime.now(timezone.utc)
        signals = 0
        login = (
            self.db.query(UserLogin)
            .filter(UserLogin.user_id == user_id, UserLogin.logged_at >= today - timedelta(days=7))
            .first()
        )
        if login:
            signals += 1
        shopee = self.db.query(ShopeeIntegration).filter(
            ShopeeIntegration.user_id == user_id, ShopeeIntegration.is_active.is_(True)
        ).first()
        fb = self.db.query(FacebookIntegration).filter(
            FacebookIntegration.user_id == user_id, FacebookIntegration.is_active.is_(True)
        ).first()
        if shopee or fb:
            signals += 1
        recent_data = (
            self.db.query(DatasetRow)
            .filter(DatasetRow.user_id == user_id, DatasetRow.date >= (today - timedelta(days=7)).date())
            .first()
        )
        if not recent_data:
            recent_data = (
                self.db.query(AdSpend)
                .filter(AdSpend.user_id == user_id, AdSpend.date >= (today - timedelta(days=7)).date())
                .first()
            )
        if recent_data:
            signals += 1
        if signals >= 3:
            return "green"
        if signals == 2:
            return "yellow"
        return "red"

    def list_clients(self, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        actives_map = {_subscriber_key(e): e for e in self.active_subscribers()}
        # also include inactive with latest event
        all_latest = _latest_by_subscriber(self._all_events())
        rows = []
        q = (filters.get("q") or "").strip().lower()
        for key, ev in all_latest.items():
            uid = ev.user_id
            user = self.db.query(User).filter(User.id == uid).first() if uid else None
            if not user and ev.customer_email:
                user = self.db.query(User).filter(User.email == ev.customer_email).first()
                uid = user.id if user else None

            is_active = key in actives_map
            status = "ativo" if is_active else "inativo"
            if (ev.subscription_status or "").lower() in ("canceled", "cancelled") and is_active:
                status = "cancelado_com_acesso"

            last_login = None
            if uid:
                ll = (
                    self.db.query(UserLogin)
                    .filter(UserLogin.user_id == uid)
                    .order_by(UserLogin.logged_at.desc())
                    .first()
                )
                last_login = ll.logged_at.isoformat() if ll else None

            has_shopee = bool(
                uid
                and self.db.query(ShopeeIntegration)
                .filter(ShopeeIntegration.user_id == uid, ShopeeIntegration.is_active.is_(True))
                .first()
            )
            has_fb = bool(
                uid
                and self.db.query(FacebookIntegration)
                .filter(FacebookIntegration.user_id == uid, FacebookIntegration.is_active.is_(True))
                .first()
            )

            paid_total_net = (
                self.db.query(func.coalesce(func.sum(SubscriptionEvent.amount_net_cents), 0))
                .filter(
                    SubscriptionEvent.event_type.in_(PAID_EVENTS),
                    (
                        (SubscriptionEvent.subscription_id == ev.subscription_id)
                        if ev.subscription_id
                        else (SubscriptionEvent.customer_email == ev.customer_email)
                    ),
                )
                .scalar()
            )

            name = ev.customer_name or (user.name if user else None) or ""
            email = ev.customer_email or (user.email if user else "") or ""
            cpf = ev.customer_cpf or ""

            if q and q not in name.lower() and q not in email.lower() and q not in cpf.lower():
                continue

            item = {
                "user_id": uid,
                "name": name,
                "email": email,
                "cpf": cpf,
                "phone": ev.customer_phone,
                "plan": _normalize_plan_label(ev.plan_name, ev.plan_id),
                "frequency": ev.plan_frequency,
                "status": status,
                "started_at": (ev.subscription_start or ev.received_at).isoformat() if (ev.subscription_start or ev.received_at) else None,
                "next_payment": ev.next_payment.isoformat() if ev.next_payment else None,
                "access_until": ev.access_until.isoformat() if ev.access_until else None,
                "total_paid_net_cents": int(paid_total_net or 0),
                "last_login_at": last_login,
                "integrations": {"shopee": has_shopee, "facebook": has_fb},
                "semaphore": self._semaphore(uid),
                "subscription_id": ev.subscription_id,
            }

            # filters
            if filters.get("status") and filters["status"] != status:
                continue
            if filters.get("plan") and filters["plan"] != item["plan"]:
                continue
            if filters.get("expiring_7d"):
                if not ev.next_payment:
                    continue
                d = ev.next_payment.date()
                today = datetime.now(timezone.utc).date()
                if not (today <= d <= today + timedelta(days=7)):
                    continue
            if filters.get("never_connected") and (has_shopee or has_fb):
                continue
            if filters.get("payment_failed"):
                qf = self.db.query(SubscriptionEvent).filter(
                    SubscriptionEvent.event_type.in_(FAILED_PAY_EVENTS)
                )
                if ev.subscription_id:
                    qf = qf.filter(SubscriptionEvent.subscription_id == ev.subscription_id)
                else:
                    qf = qf.filter(SubscriptionEvent.customer_email == ev.customer_email)
                lf = qf.order_by(SubscriptionEvent.received_at.desc()).first()
                if not lf or not lf.received_at:
                    continue
                if (datetime.now(timezone.utc) - lf.received_at).days > 14:
                    continue
            if filters.get("no_login_10d"):
                today = datetime.now(timezone.utc).date()
                if last_login:
                    try:
                        ld = datetime.fromisoformat(last_login.replace("Z", "+00:00")).date()
                        if (today - ld).days <= 10:
                            continue
                    except ValueError:
                        continue
                # sem login → inclui

            rows.append(item)
        return rows

    def client_detail(self, user_id: int) -> Optional[Dict[str, Any]]:
        user = self.db.query(User).filter(User.id == user_id).first()
        if not user:
            return None
        events = (
            self.db.query(SubscriptionEvent)
            .filter(
                (SubscriptionEvent.user_id == user_id)
                | (SubscriptionEvent.customer_email == user.email)
            )
            .order_by(SubscriptionEvent.received_at.desc())
            .all()
        )
        latest = events[0] if events else None
        clients = self.list_clients({"q": user.email})
        base = clients[0] if clients else {
            "user_id": user_id,
            "name": user.name,
            "email": user.email,
        }
        logins = (
            self.db.query(UserLogin)
            .filter(UserLogin.user_id == user_id, UserLogin.logged_at >= datetime.now(timezone.utc) - timedelta(days=30))
            .order_by(UserLogin.logged_at.asc())
            .all()
        )
        shopee = self.db.query(ShopeeIntegration).filter(ShopeeIntegration.user_id == user_id).first()
        fb = self.db.query(FacebookIntegration).filter(FacebookIntegration.user_id == user_id).first()
        camps = self.db.query(func.count(Campaign.id)).filter(Campaign.user_id == user_id).scalar() or 0
        from datetime import date as date_cls
        start_30 = date_cls.today() - timedelta(days=30)
        commission = (
            self.db.query(func.coalesce(func.sum(DatasetRow.commission), 0))
            .filter(DatasetRow.user_id == user_id, DatasetRow.date >= start_30)
            .scalar()
        )
        spend = (
            self.db.query(func.coalesce(func.sum(AdSpend.amount), 0))
            .filter(AdSpend.user_id == user_id, AdSpend.date >= start_30)
            .scalar()
        )
        return {
            **base,
            "timeline": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "received_at": e.received_at.isoformat() if e.received_at else None,
                    "amount_net_cents": e.amount_net_cents,
                    "amount_gross_cents": e.amount_gross_cents,
                    "plan_name": e.plan_name,
                    "is_plan_change": e.is_plan_change,
                }
                for e in events
            ],
            "subscription_block": {
                "plan": _normalize_plan_label(latest.plan_name if latest else None, latest.plan_id if latest else None),
                "frequency": latest.plan_frequency if latest else None,
                "status": latest.subscription_status if latest else None,
                "access_until": latest.access_until.isoformat() if latest and latest.access_until else None,
                "next_payment": latest.next_payment.isoformat() if latest and latest.next_payment else None,
                "payment_method": latest.payment_method if latest else None,
                "has_access": latest.has_access if latest else None,
            },
            "usage": {
                "logins_30d": [
                    {"at": l.logged_at.isoformat(), "ip": l.ip} for l in logins
                ],
                "shopee_last_sync": shopee.last_sync_at.isoformat() if shopee and shopee.last_sync_at else None,
                "facebook_last_sync": fb.last_sync_at.isoformat() if fb and getattr(fb, "last_sync_at", None) else None,
                "campaigns_count": int(camps),
                "commission_30d": float(commission or 0),
                "spend_30d": float(spend or 0),
            },
            "contact": {
                "email": user.email,
                "phone": latest.customer_phone if latest else None,
                "cpf": latest.customer_cpf if latest else user.cpf_cnpj,
            },
        }
