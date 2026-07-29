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
from app.core.plans import list_price_cents

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


def _dedupe_by_charge(events: List[SubscriptionEvent]) -> List[SubscriptionEvent]:
    """A Kiwify manda mais de um webhook (order_approved + subscription_renewed) pra
    MESMA cobrança — mesmo order_id, mesmo charge_amount. Somar por evento dobra o
    faturamento. Aqui cada cobrança real (order_id) conta uma vez só. Eventos sem
    order_id (raro pra PAID_EVENTS) contam individualmente — não há como colidir."""
    seen: set = set()
    result: List[SubscriptionEvent] = []
    for ev in events:
        if ev.order_id:
            if ev.order_id in seen:
                continue
            seen.add(ev.order_id)
        result.append(ev)
    return result


def _charge_as_cents(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    # float reais → cents; ints já em cents (ou strings numéricas grandes) passam direto
    try:
        f = float(value)
        return int(round(f * 100)) if abs(f) < 10000 else int(round(f))
    except (TypeError, ValueError):
        return 0


def _parse_charge_dt(ch: dict):
    from app.services.subscription_event_recorder import _parse_dt

    raw = ch.get("approved_date") or ch.get("created_at") or ch.get("date")
    return _parse_dt(raw)


def _charges_completed_for_event(ev) -> list:
    completed = getattr(ev, "charges_completed", None)
    if isinstance(completed, list) and completed:
        return completed
    raw = getattr(ev, "raw_payload", None)
    if not isinstance(raw, dict):
        return []
    for key in ("Subscription", "subscription"):
        sub = raw.get(key)
        if not isinstance(sub, dict):
            continue
        charges = sub.get("charges")
        if not isinstance(charges, dict):
            continue
        completed = charges.get("completed")
        if isinstance(completed, list):
            return completed
    return []


def extract_paid_charges_union(events) -> list[dict]:
    """Une charges.completed de vários webhooks; dedupe por order_id; só status paid."""
    by_id: dict[str, dict] = {}
    for ev in events:
        for ch in _charges_completed_for_event(ev):
            if not isinstance(ch, dict):
                continue
            if (ch.get("status") or "").lower() != "paid":
                continue
            oid = ch.get("order_id")
            if not oid:
                continue
            commissions = ch.get("Commissions") or ch.get("commissions") or {}
            net = _charge_as_cents(
                commissions.get("my_commission")
                or ch.get("my_commission")
                or ch.get("amount")
            )
            plan = _normalize_plan_label(getattr(ev, "plan_name", None), getattr(ev, "plan_id", None))
            freq = getattr(ev, "plan_frequency", None) or "monthly"
            table = list_price_cents(plan, freq)

            raw_gross = _charge_as_cents(
                commissions.get("charge_amount") or ch.get("charge_amount")
            )
            raw_fee = _charge_as_cents(
                commissions.get("kiwify_fee") or ch.get("kiwify_fee") or ch.get("fee")
            )

            if table is not None:
                gross = table
            elif raw_gross and raw_fee:
                gross = raw_gross
            else:
                gross = net or raw_gross

            fee = raw_fee if raw_fee else max(gross - net, 0)

            by_id[str(oid)] = {
                "order_id": str(oid),
                "net_cents": net,
                "gross_cents": gross,
                "fee_cents": fee,
                "paid_at": _parse_charge_dt(ch),
                "plan": plan,
                "frequency": freq,
            }
    return list(by_id.values())


def total_paid_net_from_charges(events) -> int:
    return sum(c["net_cents"] for c in extract_paid_charges_union(events))


def revenue_from_charges_for_month(events, year: int, month: int) -> dict:
    net = gross = 0
    for c in extract_paid_charges_union(events):
        dt = c.get("paid_at")
        if not dt:
            continue
        d = dt.date() if hasattr(dt, "date") else dt
        if d.year == year and d.month == month:
            net += c["net_cents"]
            gross += c["gross_cents"]
    return {"net": net, "gross": gross}


def _subscribers_with_charges_completed(events) -> set:
    """Assinantes com ≥1 cobrança paid na união — array só com waiting_payment NÃO conta."""
    return {
        _subscriber_key(ev)
        for ev in events
        if extract_paid_charges_union([ev])
    }


def _paid_total_for_events(events, paid_events=None) -> int:
    """Total pago líquido: união de charges paid se houver; senão PAID_EVENTS dedupe."""
    if paid_events is None:
        paid_events = PAID_EVENTS
    if extract_paid_charges_union(events):
        return total_paid_net_from_charges(events)
    paid = [e for e in events if (e.event_type or "").lower() in paid_events]
    return sum((e.amount_net_cents or 0) for e in _dedupe_by_charge(paid))


def _fees_from_charges_for_month(events, year: int, month: int) -> int:
    fees = 0
    for c in extract_paid_charges_union(events):
        dt = c.get("paid_at")
        if not dt:
            continue
        d = dt.date() if hasattr(dt, "date") else dt
        if d.year == year and d.month == month:
            fees += c.get("fee_cents") if c.get("fee_cents") is not None else max(
                (c["gross_cents"] or 0) - (c["net_cents"] or 0), 0
            )
    return fees


def _legacy_paid_in_month(events, year: int, month: int, skip_keys: set) -> List[SubscriptionEvent]:
    """PAID_EVENTS por received_at — só assinantes sem cobrança paid na união (legado)."""
    start, end = _month_bounds(year, month)
    paid = [
        e
        for e in events
        if (e.event_type or "").lower() in PAID_EVENTS
        and e.received_at is not None
        and start <= e.received_at <= end
        and _subscriber_key(e) not in skip_keys
    ]
    return _dedupe_by_charge(paid)


# Pra campos de ESTADO da assinatura (next_payment, access_until, status) — não pra
# valor pago. A Kiwify manda order_approved e subscription_renewed quase juntos pra
# mesma renovação, mas order_approved às vezes carrega o next_payment ANTIGO (de
# antes da renovação processar) enquanto subscription_renewed já traz o novo — e
# pode chegar alguns milissegundos DEPOIS. "Mais recente por received_at" sozinho
# pega o evento errado nesse caso. Eventos que mudam o estado da assinatura
# (renewed/late/canceled/refund/chargeback) ficam no mesmo tier — received_at
# decide; order_approved fica abaixo pra não sobrescrever um renew quase
# simultâneo com next_payment velho.
_SUBSCRIBER_STATE_PRIORITY = {
    "subscription_canceled": 2,
    "subscription_late": 2,
    "subscription_renewed": 2,
    "order_refunded": 2,
    "order_chargedback": 2,
    "chargeback": 2,
    "order_approved": 1,
    "compra_aprovada": 1,
}


def _subscriber_state_sort_key(ev: SubscriptionEvent) -> tuple:
    priority = _SUBSCRIBER_STATE_PRIORITY.get((ev.event_type or "").lower(), 0)
    received = ev.received_at or datetime.min.replace(tzinfo=timezone.utc)
    return (priority, received)


def _latest_by_subscriber(events: List[SubscriptionEvent]) -> Dict[str, SubscriptionEvent]:
    latest: Dict[str, SubscriptionEvent] = {}
    for ev in sorted(events, key=_subscriber_state_sort_key):
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


def _client_display_status(ev: SubscriptionEvent, is_active: bool) -> str:
    """Status de exibição na lista/ficha — late ≠ churn; late com acesso pode ser ativo no count."""
    etype = (ev.event_type or "").lower()
    sub_st = (ev.subscription_status or "").lower()
    is_late = etype == "subscription_late" or sub_st == "waiting_payment"
    is_canceled = etype == "subscription_canceled" or sub_st in ("canceled", "cancelled")

    if is_canceled and is_active:
        return "cancelado_com_acesso"
    if is_canceled:
        return "inativo"
    if is_late:
        return "atrasado"
    if is_active:
        return "ativo"
    return "inativo"


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
        all_events = self._all_events()
        charges_rev = revenue_from_charges_for_month(all_events, year, month)
        skip = _subscribers_with_charges_completed(all_events)
        legacy = _legacy_paid_in_month(all_events, year, month, skip)
        gross = charges_rev["gross"] + sum((e.amount_gross_cents or 0) for e in legacy)
        net = charges_rev["net"] + sum((e.amount_net_cents or 0) for e in legacy)

        refunds = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(REFUND_EVENTS),
                func.coalesce(SubscriptionEvent.refunded_at, SubscriptionEvent.received_at) >= start,
                func.coalesce(SubscriptionEvent.refunded_at, SubscriptionEvent.received_at) <= end,
            )
            .all()
        )
        refunds = _dedupe_by_charge(refunds)
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
        first = self.db.query(func.min(SubscriptionEvent.received_at)).scalar()
        if not first:
            return {"mrr": [], "revenue": []}

        start_y, start_m = first.year, first.month

        all_events = self.db.query(SubscriptionEvent).all()
        for c in extract_paid_charges_union(all_events):
            dt = c.get("paid_at")
            if not dt:
                continue
            d = dt.date() if hasattr(dt, "date") else dt
            if (d.year, d.month) < (start_y, start_m):
                start_y, start_m = d.year, d.month

        mrr_series: List[Dict[str, Any]] = []
        y, m = start_y, start_m
        while (y, m) <= (today.year, today.month):
            end_day = monthrange(y, m)[1]
            as_of = date(y, m, end_day)
            if (y, m) == (today.year, today.month):
                as_of = today
            actives = self.active_subscribers(as_of=as_of)
            mrr = self.mrr_cents(actives)
            mrr_series.append({
                "month": f"{y:04d}-{m:02d}",
                "net": mrr["net"],
                "gross": mrr["gross"],
            })
            m += 1
            if m > 12:
                m, y = 1, y + 1

        rev_series: List[Dict[str, Any]] = []
        y, m = start_y, start_m
        while (y, m) <= (today.year, today.month):
            rev = self.revenue_for_month(y, m)
            rev_series.append({
                "month": f"{y:04d}-{m:02d}",
                "net": rev["net"],
                "gross": rev["gross"],
            })
            m += 1
            if m > 12:
                m, y = 1, y + 1

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
        has_integration = bool(shopee or fb)
        if signals >= 3:
            return "green"
        if signals == 2:
            return "yellow"
        # 0-1 sinal: integração conectada nunca é vermelho sozinha — histórico de login
        # começou do zero no deploy, "sem login" hoje é normal e não pode derrubar quem
        # já deu o passo de conectar Shopee/Meta.
        if has_integration:
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
            status = _client_display_status(ev, is_active)

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

            sub_filter = (
                (SubscriptionEvent.subscription_id == ev.subscription_id)
                if ev.subscription_id
                else (SubscriptionEvent.customer_email == ev.customer_email)
            )
            sub_events = self.db.query(SubscriptionEvent).filter(sub_filter).all()
            # Preferir união de charges paid; fallback legado se união vazia
            # (sem array OU só waiting_payment / não-paid).
            paid_total_net = _paid_total_for_events(sub_events)

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
                "card_rejection_reason": getattr(ev, "card_rejection_reason", None),
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
                    "card_rejection_reason": e.card_rejection_reason,
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
                "card_rejection_reason": latest.card_rejection_reason if latest else None,
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
