"""Gravação append-only de webhooks Kiwify em subscription_events."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.subscription_event import SubscriptionEvent
from app.models.user import User

logger = logging.getLogger(__name__)


def _parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        # epoch seconds
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            return None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # dd/mm/yyyy or ISO
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%f%z",
            "%Y-%m-%dT%H:%M:%S%z",
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
            "%d/%m/%Y",
            "%d/%m/%Y %H:%M:%S",
        ):
            try:
                dt = datetime.strptime(s.replace("Z", "+0000") if fmt.endswith("%z") and s.endswith("Z") else s, fmt)
                return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _as_cents(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def build_dedupe_key(order_id: Optional[str], event_type: str, approved_date: Optional[datetime]) -> str:
    ad = approved_date.isoformat() if approved_date else ""
    return f"{order_id or ''}|{event_type or ''}|{ad}"


def extract_event_fields(payload: Dict[str, Any], event_type: str) -> Dict[str, Any]:
    order = payload.get("order") if isinstance(payload.get("order"), dict) else payload
    customer = order.get("Customer") or order.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    subscription = order.get("Subscription") or order.get("subscription") or {}
    if not isinstance(subscription, dict):
        subscription = {}
    plan = subscription.get("plan") or {}
    if not isinstance(plan, dict):
        plan = {}
    commissions = order.get("Commissions") or order.get("commissions") or {}
    if not isinstance(commissions, dict):
        commissions = {}
    access = subscription.get("customer_access") or {}
    if not isinstance(access, dict):
        access = {}
    charges = subscription.get("charges") or {}
    if not isinstance(charges, dict):
        charges = {}
    # Array cumulativo de cobranças já pagas dessa assinatura — cada entrada tem seu
    # próprio order_id (diferente do order_id do webhook em si). Fonte de verdade
    # pra "cobrança distinta" — ver migration 038.
    charges_completed = charges.get("completed")
    if not isinstance(charges_completed, list):
        charges_completed = None

    approved = _parse_dt(order.get("approved_date") or order.get("created_at"))
    refunded = _parse_dt(order.get("refunded_at"))
    access_until = _parse_dt(access.get("access_until") or subscription.get("access_until"))
    next_payment = _parse_dt(subscription.get("next_payment"))
    start_date = _parse_dt(subscription.get("start_date"))
    deposit = _parse_dt(commissions.get("deposit_date"))

    has_access = access.get("has_access")
    if has_access is None:
        has_access = None
    else:
        has_access = bool(has_access)

    email = (customer.get("email") or "").strip().lower() or None

    return {
        "event_type": (event_type or "").strip().lower() or "unknown",
        "order_id": order.get("order_id"),
        "order_ref": order.get("order_ref"),
        "order_status": order.get("order_status"),
        "subscription_id": order.get("subscription_id") or subscription.get("id"),
        "customer_email": email,
        "customer_name": customer.get("full_name") or customer.get("name"),
        "customer_cpf": customer.get("CPF") or customer.get("cpf"),
        "customer_phone": customer.get("mobile") or customer.get("phone"),
        "plan_id": str(plan.get("id")) if plan.get("id") is not None else None,
        "plan_name": plan.get("name"),
        "plan_frequency": (plan.get("frequency") or plan.get("charge_frequency") or "").lower() or None,
        "amount_gross_cents": _as_cents(commissions.get("charge_amount")),
        "fee_cents": _as_cents(commissions.get("kiwify_fee")),
        "amount_net_cents": _as_cents(commissions.get("my_commission")),
        "payment_method": order.get("payment_method"),
        "subscription_status": subscription.get("status"),
        "has_access": has_access,
        "access_until": access_until,
        "next_payment": next_payment,
        "subscription_start": start_date,
        "approved_date": approved,
        "refunded_at": refunded,
        "funds_status": commissions.get("funds_status"),
        "deposit_date": deposit,
        "charges_completed": charges_completed,
        "card_rejection_reason": (
            order.get("card_rejection_reason") or payload.get("card_rejection_reason")
        ),
        "dedupe_key": build_dedupe_key(order.get("order_id"), (event_type or "").strip().lower(), approved),
    }


def _mark_plan_change_if_needed(
    db: Session, fields: Dict[str, Any], reference_time: Optional[datetime] = None
) -> bool:
    """Anti-churn falso: mesmo CPF cancela e reassina em janela curta.

    Duas regras, mesmo mecanismo (`is_plan_change=True` nos dois eventos
    envolvidos, filtrado tanto em churn_for_month quanto em new_subscriptions):
    - Continuação: cancela e reassina o MESMO plano em ≤1 dia (troca de forma
      de pagamento, não saiu de verdade — ex.: mesma pessoa, mesmo instante).
    - Upgrade/downgrade: cancela e reassina plano DIFERENTE em ≤30 dias.

    `reference_time` (default None → datetime.now()) existe pra permitir
    processar eventos HISTÓRICOS com a janela ancorada na data do próprio
    evento, não em "agora" — sem isso, importar uma linha de abril nunca
    dispara a regra, porque abril já estaria fora da janela de 30 dias
    contados da data de execução do import.
    """
    cpf = fields.get("customer_cpf")
    event = fields.get("event_type") or ""
    if not cpf:
        return False

    from datetime import timedelta

    agora = reference_time or datetime.now(timezone.utc)
    cutoff = agora - timedelta(days=30)
    recent = (
        db.query(SubscriptionEvent)
        .filter(
            SubscriptionEvent.customer_cpf == cpf,
            SubscriptionEvent.received_at >= cutoff,
            SubscriptionEvent.received_at <= agora,
            SubscriptionEvent.event_type == "subscription_canceled",
        )
        .order_by(SubscriptionEvent.received_at.desc())
        .limit(20)
        .all()
    )

    paid_like = event in ("order_approved", "subscription_renewed", "compra_aprovada")
    if not paid_like:
        return False

    for ev in recent:
        recebido = ev.received_at
        if recebido is not None and recebido.tzinfo is None:
            recebido = recebido.replace(tzinfo=timezone.utc)
        gap = agora - (recebido or agora)
        mesmo_plano = (ev.plan_name or "") == (fields.get("plan_name") or "") and (
            ev.plan_frequency or ""
        ) == (fields.get("plan_frequency") or "")
        if mesmo_plano and gap <= timedelta(days=1):
            return True  # continuação
        if not mesmo_plano and gap <= timedelta(days=30):
            return True  # upgrade/downgrade
    return False


def record_subscription_event(
    db: Session,
    payload: Dict[str, Any],
    event_type: str,
) -> Optional[SubscriptionEvent]:
    """Insere evento. Retorna None se duplicado. Nunca quebra o webhook por dedupe."""
    try:
        fields = extract_event_fields(payload, event_type)
        existing = (
            db.query(SubscriptionEvent)
            .filter(SubscriptionEvent.dedupe_key == fields["dedupe_key"])
            .first()
        )
        if existing:
            logger.info("subscription_event duplicate skipped type=%s", event_type)
            return None

        user_id = None
        if fields.get("customer_email"):
            user = db.query(User).filter(User.email == fields["customer_email"]).first()
            if user:
                user_id = user.id

        is_plan_change = _mark_plan_change_if_needed(db, fields)
        if is_plan_change and fields.get("customer_cpf"):
            recent_cancel = (
                db.query(SubscriptionEvent)
                .filter(
                    SubscriptionEvent.customer_cpf == fields["customer_cpf"],
                    SubscriptionEvent.event_type == "subscription_canceled",
                    SubscriptionEvent.is_plan_change.is_(False),
                )
                .order_by(SubscriptionEvent.received_at.desc())
                .first()
            )
            if recent_cancel:
                recent_cancel.is_plan_change = True

        row = SubscriptionEvent(
            event_type=fields["event_type"],
            order_id=fields.get("order_id"),
            order_ref=fields.get("order_ref"),
            order_status=fields.get("order_status"),
            subscription_id=fields.get("subscription_id"),
            customer_email=fields.get("customer_email"),
            customer_name=fields.get("customer_name"),
            customer_cpf=fields.get("customer_cpf"),
            customer_phone=fields.get("customer_phone"),
            plan_id=fields.get("plan_id"),
            plan_name=fields.get("plan_name"),
            plan_frequency=fields.get("plan_frequency"),
            amount_gross_cents=fields.get("amount_gross_cents"),
            fee_cents=fields.get("fee_cents"),
            amount_net_cents=fields.get("amount_net_cents"),
            payment_method=fields.get("payment_method"),
            subscription_status=fields.get("subscription_status"),
            has_access=fields.get("has_access"),
            access_until=fields.get("access_until"),
            next_payment=fields.get("next_payment"),
            subscription_start=fields.get("subscription_start"),
            approved_date=fields.get("approved_date"),
            refunded_at=fields.get("refunded_at"),
            funds_status=fields.get("funds_status"),
            deposit_date=fields.get("deposit_date"),
            charges_completed=fields.get("charges_completed"),
            card_rejection_reason=fields.get("card_rejection_reason"),
            user_id=user_id,
            is_plan_change=is_plan_change,
            raw_payload=payload,
            dedupe_key=fields["dedupe_key"],
        )
        db.add(row)
        db.flush()

        # Backfill: assim que a Kiwify manda um subscription_id real pra um CPF
        # que já tinha histórico órfão (ex.: importado sem subscription_id, ou
        # de antes desse campo existir), "adota" retroativamente esse histórico
        # pro mesmo grupo — sem isso, a pessoa vira "2 assinantes" a partir da
        # primeira renovação real pós-import (o histórico fica preso na chave
        # por CPF, o evento novo abre uma chave por subscription_id à parte).
        if fields.get("subscription_id") and fields.get("customer_cpf"):
            db.query(SubscriptionEvent).filter(
                SubscriptionEvent.customer_cpf == fields["customer_cpf"],
                SubscriptionEvent.subscription_id.is_(None),
            ).update({"subscription_id": fields["subscription_id"]}, synchronize_session=False)

        logger.info(
            "subscription_event recorded type=%s order=%s",
            fields["event_type"],
            fields.get("order_id"),
        )
        return row
    except IntegrityError:
        logger.info("subscription_event race duplicate type=%s", event_type)
        db.rollback()
        return None
    except Exception as exc:  # noqa: BLE001
        logger.error("subscription_event record failed: %s", exc, exc_info=True)
        return None
