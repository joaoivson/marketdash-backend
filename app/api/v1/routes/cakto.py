from typing import Any, Dict, Optional, Set
from datetime import datetime, timedelta
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.security import get_password_hash
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.services.subscription_service import SubscriptionService
from app.services.auth_service import AuthService
from app.services.cakto_service import create_checkout_url

logger = logging.getLogger(__name__)

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


def _extract_customer_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados do cliente do payload do webhook."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    customer = data.get("customer") or data.get("buyer") or {}
    
    if not isinstance(customer, dict):
        customer = {}
    
    return {
        "email": customer.get("email") or data.get("email"),
        "name": customer.get("name") or customer.get("full_name") or data.get("name"),
        "cpf_cnpj": customer.get("docNumber") or customer.get("cpf_cnpj") or data.get("cpf_cnpj"),
        "customer_id": customer.get("id") or data.get("customer_id"),
        "phone": customer.get("phone") or data.get("phone"),
    }


def _extract_transaction_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados da transação do payload do webhook."""
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    
    return {
        "transaction_id": data.get("id") or data.get("transaction_id"),
        "amount": data.get("amount"),
        "status": data.get("status"),
        "payment_method": data.get("paymentMethod") or data.get("payment_method"),
    }


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
    """Webhook do Cakto para processar eventos de assinatura."""
    logger.info("Webhook Cakto recebido")
    
    # Validação do secret
    if settings.CAKTO_WEBHOOK_SECRET:
        secret = (
            request.headers.get("x-cakto-secret")
            or request.headers.get("x-webhook-secret")
            or request.headers.get("x-cakto-signature")
        )
        # Também verificar no payload (fallback conforme documentação)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")
        
        payload_secret = payload.get("secret")
        if secret != settings.CAKTO_WEBHOOK_SECRET and payload_secret != settings.CAKTO_WEBHOOK_SECRET:
            logger.warning(f"Webhook não autorizado. Secret esperado: {settings.CAKTO_WEBHOOK_SECRET[:10]}...")
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook não autorizado")
    else:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload inválido")

    event = _extract_event(payload)
    email = _extract_email(payload)
    product_id = _extract_product_id(payload)
    action = _infer_action(payload, event)

    logger.info(f"Evento processado: {event}, email: {email}, action: {action}")

    if not email:
        return {"status": "ignored", "reason": "email_not_found"}
    if not _product_allowed(product_id):
        return {"status": "ignored", "reason": "product_not_allowed"}
    if not action:
        return {"status": "ignored", "reason": "event_not_mapped"}

    # Extrair dados do cliente e transação
    customer_data = _extract_customer_data(payload)
    transaction_data = _extract_transaction_data(payload)
    
    # Buscar ou criar usuário
    user = db.query(User).filter(User.email.ilike(email)).first()
    user_created = False
    if not user:
        # Criar usuário a partir dos dados do Cakto
        logger.info(f"Criando novo usuário do Cakto: {email}")
        auth_service = AuthService(UserRepository(db))
        user = auth_service.register_from_cakto(
            email=customer_data["email"],
            name=customer_data["name"],
            cpf_cnpj=customer_data["cpf_cnpj"]
        )
        user_created = True
        logger.info(f"Usuário criado com ID: {user.id}")

    # Atualizar subscription
    subscription_service = SubscriptionService(SubscriptionRepository(db))
    
    expires_at = None
    if action == "activate":
        # Definir expiração para 30 dias a partir de agora
        expires_at = datetime.utcnow() + timedelta(days=30)
    
    subscription = subscription_service.set_active(
        user_id=user.id,
        plan="marketdash" if action == "activate" else "free",
        is_active=(action == "activate"),
        cakto_customer_id=customer_data.get("customer_id"),
        cakto_transaction_id=transaction_data.get("transaction_id"),
        expires_at=expires_at,
    )
    
    # Atualizar last_validation_at quando ativar
    if action == "activate":
        subscription.last_validation_at = datetime.utcnow()
        db.commit()
        db.refresh(subscription)

    logger.info(f"Subscription atualizada para user {user.id}: active={subscription.is_active}")

    return {
        "status": "ok",
        "action": action,
        "user_id": user.id,
        "subscription_active": subscription.is_active,
        "user_created": user_created,
    }


@router.get("/checkout-url")
def get_checkout_url(
    email: str = Query(..., description="Email do usuário"),
    name: str = Query(None, description="Nome do usuário"),
    cpf_cnpj: str = Query(None, description="CPF/CNPJ do usuário"),
):
    """Gera URL de checkout do Cakto com parâmetros pré-preenchidos."""
    checkout_url = create_checkout_url(email=email, name=name, cpf_cnpj=cpf_cnpj)
    return {"checkout_url": checkout_url}
