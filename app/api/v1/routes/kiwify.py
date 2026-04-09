"""
Webhook e rotas da Kiwify.

Recebe eventos de assinatura da Kiwify e processa ativações/desativações.
"""

from typing import Any, Dict, Optional, Set
from datetime import datetime, timezone
import logging
import traceback

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService
from app.services.webhook_helpers import (
    find_or_create_user,
    send_subscription_email,
    calculate_expires_at,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kiwify"])

# Eventos Kiwify mapeados
ACTIVATE_EVENTS: Set[str] = {
    "compra_aprovada",
    "subscription_renewed",
}

DEACTIVATE_EVENTS: Set[str] = {
    "subscription_canceled",
    "subscription_late",
    "compra_reembolsada",
    "chargeback",
}

LOG_ONLY_EVENTS: Set[str] = {
    "compra_recusada",
    "boleto_gerado",
    "pix_gerado",
    "carrinho_abandonado",
}


def _extract_event(payload: Dict[str, Any]) -> str:
    """Extrai o tipo de evento do payload Kiwify."""
    # Kiwify envia o trigger name como campo de nível raiz
    for key in ("webhook_event_type", "event", "type", "event_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    # Fallback: inferir do order_status
    order_status = (payload.get("order_status") or "").lower()
    if order_status in ("approved", "paid"):
        return "compra_aprovada"
    if order_status in ("refunded",):
        return "compra_reembolsada"
    if order_status in ("chargedback",):
        return "chargeback"
    return ""


def _extract_email(payload: Dict[str, Any]) -> Optional[str]:
    """Extrai email do Customer no payload Kiwify."""
    customer = payload.get("Customer") or payload.get("customer") or {}
    if isinstance(customer, dict):
        email = customer.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip().lower()
    # Fallback: campo de nível raiz
    email = payload.get("email") or payload.get("customer_email")
    if isinstance(email, str) and email.strip():
        return email.strip().lower()
    return None


def _extract_customer_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados do cliente do payload Kiwify."""
    customer = payload.get("Customer") or payload.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}
    return {
        "email": customer.get("email") or payload.get("email"),
        "name": customer.get("full_name") or customer.get("name"),
        "cpf_cnpj": customer.get("cpf") or customer.get("cnpj") or customer.get("cpf_cnpj"),
        "customer_id": customer.get("id"),
        "phone": customer.get("mobile") or customer.get("phone"),
    }


def _extract_transaction_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados da transação do payload Kiwify."""
    subscription = payload.get("Subscription") or payload.get("subscription") or {}
    product = payload.get("Product") or payload.get("product") or {}
    commissions = payload.get("Commissions") or payload.get("commissions") or {}

    if not isinstance(subscription, dict):
        subscription = {}
    if not isinstance(product, dict):
        product = {}

    # Kiwify: next_payment como due_date
    next_payment = subscription.get("next_payment")
    start_date = subscription.get("start_date")

    # Parse dates
    due_date = None
    if next_payment:
        try:
            due_date = datetime.fromisoformat(str(next_payment).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    paid_at = None
    if start_date:
        try:
            paid_at = datetime.fromisoformat(str(start_date).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            pass

    return {
        "transaction_id": payload.get("order_id"),
        "order_ref": payload.get("order_ref"),
        "amount": commissions.get("my_commission") if isinstance(commissions, dict) else None,
        "status": payload.get("order_status"),
        "subscription_status": None,
        "payment_status": payload.get("order_status"),
        "payment_method": payload.get("payment_method"),
        "due_date": due_date,
        "due_date_present": due_date is not None,
        "recurrence_period": None,  # Kiwify não envia explicitamente
        "paid_at": paid_at,
        "offer_name": product.get("product_name") or product.get("name"),
        "product_id": product.get("product_id") or product.get("id"),
    }


def _get_allowed_products() -> Set[str]:
    raw = settings.KIWIFY_SUBSCRIPTION_PRODUCT_IDS or ""
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _product_allowed(product_id: Optional[str]) -> bool:
    allowed = _get_allowed_products()
    if not allowed:
        return True
    if not product_id:
        return False
    return product_id in allowed


def _infer_action(event: str) -> Optional[str]:
    if event in ACTIVATE_EVENTS:
        return "activate"
    if event in DEACTIVATE_EVENTS:
        return "deactivate"
    if event in LOG_ONLY_EVENTS:
        return None
    return None


@router.post("/webhook")
async def kiwify_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook da Kiwify para processar eventos de assinatura."""
    try:
        logger.info("Webhook Kiwify recebido")

        body = await request.body()
        try:
            payload = await request.json()
        except Exception as json_err:
            logger.error(f"Erro ao decodificar JSON do webhook Kiwify: {str(json_err)}")
            logger.error(f"Corpo recebido: {body.decode('utf-8', errors='replace')}")
            return {"status": "error", "reason": "invalid_json"}

        # Validação do token
        if settings.KIWIFY_WEBHOOK_SECRET:
            payload_token = payload.get("token")
            header_token = request.headers.get("x-kiwify-token") or request.headers.get("x-webhook-token")
            if payload_token != settings.KIWIFY_WEBHOOK_SECRET and header_token != settings.KIWIFY_WEBHOOK_SECRET:
                logger.warning("Webhook Kiwify não autorizado: token inválido")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook não autorizado")

        event = _extract_event(payload)
        email = _extract_email(payload)
        action = _infer_action(event)

        logger.info(f"Kiwify webhook - Evento: {event}, Email: {email}, Ação: {action}")

        if not email:
            logger.warning(f"Email não encontrado no payload Kiwify. Evento: {event}")
            return {"status": "ignored", "reason": "email_not_found"}

        if not action:
            logger.info(f"Evento Kiwify '{event}' ignorado (log only ou não mapeado)")
            return {"status": "ignored", "reason": "event_not_mapped", "event": event}

        # Extrair dados
        try:
            customer_data = _extract_customer_data(payload)
            transaction_data = _extract_transaction_data(payload)
        except Exception as extract_err:
            logger.error(f"Erro ao extrair dados do payload Kiwify: {str(extract_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Erro na extração de dados")

        # Verificar filtro de produto
        product_id = transaction_data.get("product_id")
        if not _product_allowed(product_id):
            logger.warning(f"Produto Kiwify não autorizado: {product_id}")
            return {"status": "ignored", "reason": "product_not_allowed", "product_id": product_id}

        # Buscar ou criar usuário
        try:
            user, user_created, user_has_password = find_or_create_user(email, customer_data, db)
            if user_created:
                logger.info(f"Usuário {email} criado via webhook Kiwify com ID {user.id}")
        except Exception as reg_err:
            logger.error(f"Erro ao registrar usuário via webhook Kiwify: {str(reg_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao criar usuário")

        # Calcular expiração
        expires_at = transaction_data.get("due_date")
        if action == "activate":
            expires_at = calculate_expires_at(
                expires_at,
                transaction_data.get("recurrence_period"),
                transaction_data.get("paid_at"),
            )

        # Atualizar assinatura
        subscription_service = SubscriptionService(SubscriptionRepository(db))

        try:
            logger.info(f"Atualizando assinatura Kiwify para usuário {user.id} (ação: {action})")
            subscription = subscription_service.set_active(
                user_id=user.id,
                plan="marketdash" if action == "activate" else "free",
                is_active=(action == "activate"),
                expires_at=expires_at,
                # Provider fields
                provider="kiwify",
                provider_customer_id=customer_data.get("customer_id"),
                provider_transaction_id=transaction_data.get("transaction_id"),
                provider_status=transaction_data.get("status"),
                provider_offer_name=transaction_data.get("offer_name"),
                provider_due_date=transaction_data.get("due_date"),
                provider_subscription_status=transaction_data.get("subscription_status"),
                provider_payment_status=transaction_data.get("payment_status"),
                provider_payment_method=transaction_data.get("payment_method"),
                provider_order_id=transaction_data.get("transaction_id"),
            )

            if action == "activate":
                subscription.last_validation_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(subscription)

                # Enviar email via helper
                send_subscription_email(
                    user=user,
                    email=email,
                    user_created=user_created,
                    user_has_password=user_has_password,
                    customer_name=customer_data.get("name"),
                    db=db,
                )

            logger.info(f"Webhook Kiwify processado com sucesso para {email}")
            return {
                "status": "ok",
                "action": action,
                "user_id": user.id,
                "subscription_active": subscription.is_active,
                "next_payment_date": (
                    subscription.provider_due_date.isoformat()
                    if subscription.provider_due_date
                    else None
                ),
                "user_created": user_created,
            }

        except Exception as sub_err:
            logger.error(f"Erro no processamento da assinatura Kiwify: {str(sub_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(sub_err))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ERRO CRÍTICO NO WEBHOOK KIWIFY: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"Erro interno: {str(e)}"},
        )
