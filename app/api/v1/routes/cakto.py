from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService

router = APIRouter(tags=["cakto"])

ACTIVATE_EVENTS: Set[str] = {
    "subscription_created",
    "subscription_approved",
    "subscription_active",
    "purchase_approved",
    "order_paid",
    "payment_approved",
}

DEACTIVATE_EVENTS: Set[str] = {
    "subscription_canceled",
    "subscription_cancelled",
    "subscription_expired",
    "subscription_failed",
    "subscription_suspended",
    "chargeback",
    "refund",
    "payment_refunded",
    "order_refunded",
}


def _extract_event(payload: Dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    for key in ("event", "type", "event_name", "name"):
        value = payload.get(key) or data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return ""


def _extract_email(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("email", "customer_email", "buyer_email"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    customer = data.get("customer") or data.get("buyer") or {}
    if isinstance(customer, dict):
        value = customer.get("email")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    return None


def _extract_product_id(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    candidates = []
    for key in ("product_id", "offer_product_id"):
        value = data.get(key)
        if value is not None:
            candidates.append(str(value))
    product = data.get("product") or {}
    if isinstance(product, dict) and product.get("id") is not None:
        candidates.append(str(product.get("id")))
    offer = data.get("offer") or {}
    if isinstance(offer, dict):
        if offer.get("product_id") is not None:
            candidates.append(str(offer.get("product_id")))
        offer_product = offer.get("product") or {}
        if isinstance(offer_product, dict) and offer_product.get("id") is not None:
            candidates.append(str(offer_product.get("id")))
    return candidates[0] if candidates else None


def _product_allowed(product_id: Optional[str]) -> bool:
    raw = settings.CAKTO_SUBSCRIPTION_PRODUCT_IDS or ""
    allowed = {item.strip() for item in raw.split(",") if item.strip()}
    if not allowed:
        return True
    if not product_id:
        return False
    return product_id in allowed


def _infer_action(payload: Dict[str, Any], event: str) -> Optional[str]:
    if event in ACTIVATE_EVENTS:
        return "activate"
    if event in DEACTIVATE_EVENTS:
        return "deactivate"

    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    for key in ("subscription_status", "status", "payment_status"):
        value = data.get(key)
        if not isinstance(value, str):
            continue
        status_value = value.strip().lower()
        if status_value in {"active", "approved", "paid"}:
            return "activate"
        if status_value in {"canceled", "cancelled", "expired", "failed", "refunded", "chargeback"}:
            return "deactivate"
    return None


@router.post("/webhook")
async def cakto_webhook(request: Request, db: Session = Depends(get_db)):
    if settings.CAKTO_WEBHOOK_SECRET:
        secret = (
            request.headers.get("x-cakto-secret")
            or request.headers.get("x-webhook-secret")
            or request.headers.get("x-cakto-signature")
        )
        if secret != settings.CAKTO_WEBHOOK_SECRET:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook não autorizado")

    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")

    event = _extract_event(payload)
    email = _extract_email(payload)
    product_id = _extract_product_id(payload)
    action = _infer_action(payload, event)

    if not email:
        return {"status": "ignored", "reason": "email_not_found"}
    if not _product_allowed(product_id):
        return {"status": "ignored", "reason": "product_not_allowed"}
    if not action:
        return {"status": "ignored", "reason": "event_not_mapped"}

    user = db.query(User).filter(User.email.ilike(email)).first()
    if not user:
        return {"status": "ignored", "reason": "user_not_found"}

    subscription_service = SubscriptionService(SubscriptionRepository(db))
    subscription = subscription_service.set_active(
        user_id=user.id,
        plan="marketdash" if action == "activate" else "free",
        is_active=(action == "activate"),
    )

    return {
        "status": "ok",
        "action": action,
        "user_id": user.id,
        "subscription_active": subscription.is_active,
    }
