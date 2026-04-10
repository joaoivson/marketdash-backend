"""
Webhook e rotas da Kiwify.

Recebe eventos de assinatura da Kiwify e processa ativações/desativações.

Payload real da Kiwify:
{
  "url": "...",
  "signature": "...",
  "order": {
    "order_id": "...",
    "order_status": "paid|refunded|...",
    "webhook_event_type": "order_approved|order_refunded|subscription_canceled|...",
    "Product": { "product_id": "...", "product_name": "..." },
    "Customer": { "full_name": "...", "email": "...", "mobile": "...", "CPF": "..." },
    "Subscription": { "start_date": "...", "next_payment": "...", "status": "active|canceled", ... },
    ...
  }
}
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

# Eventos Kiwify (webhook_event_type reais)
ACTIVATE_EVENTS: Set[str] = {
    "order_approved",
    "subscription_renewed",
}

DEACTIVATE_EVENTS: Set[str] = {
    "subscription_canceled",
    "subscription_late",
    "order_refunded",
    "order_chargedback",
    "chargeback",
}

LOG_ONLY_EVENTS: Set[str] = {
    "order_refused",
    "boleto_created",
    "pix_created",
    "cart_abandoned",
    # Nomes em português também (triggers do painel Kiwify)
    "compra_recusada",
    "boleto_gerado",
    "pix_gerado",
    "carrinho_abandonado",
    "compra_aprovada",
    "compra_reembolsada",
}


def _extract_order(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai o objeto order do payload. Kiwify aninha tudo em payload.order."""
    order = payload.get("order")
    if isinstance(order, dict):
        return order
    # Fallback: payload pode ser o próprio order (sem wrapper)
    return payload


def _extract_event(order: Dict[str, Any]) -> str:
    """Extrai o tipo de evento do order."""
    # Campo principal
    event_type = order.get("webhook_event_type")
    if isinstance(event_type, str) and event_type.strip():
        return event_type.strip().lower()
    # Fallback: inferir do order_status
    order_status = (order.get("order_status") or "").lower()
    if order_status in ("approved", "paid"):
        return "order_approved"
    if order_status in ("refunded",):
        return "order_refunded"
    if order_status in ("chargedback",):
        return "order_chargedback"
    return ""


def _map_event_to_action(event: str, order: Dict[str, Any]) -> Optional[str]:
    """Mapeia evento para ação (activate/deactivate/None)."""
    if event in ACTIVATE_EVENTS:
        return "activate"
    if event in DEACTIVATE_EVENTS:
        return "deactivate"
    if event in LOG_ONLY_EVENTS:
        return None

    # Fallback: verificar Subscription.status
    subscription = order.get("Subscription") or order.get("subscription") or {}
    if isinstance(subscription, dict):
        sub_status = (subscription.get("status") or "").lower()
        if sub_status == "active":
            return "activate"
        if sub_status in ("canceled", "cancelled", "expired"):
            return "deactivate"

    # Fallback: nomes em português dos triggers
    if event in ("compra_aprovada",):
        return "activate"
    if event in ("compra_reembolsada",):
        return "deactivate"

    return None


def _extract_email(order: Dict[str, Any]) -> Optional[str]:
    """Extrai email do Customer."""
    customer = order.get("Customer") or order.get("customer") or {}
    if isinstance(customer, dict):
        email = customer.get("email")
        if isinstance(email, str) and email.strip():
            return email.strip().lower()
    return None


def _extract_customer_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados do cliente do order Kiwify."""
    customer = order.get("Customer") or order.get("customer") or {}
    if not isinstance(customer, dict):
        customer = {}

    # CPF pode vir como "CPF" (maiúsculo) ou "cpf"
    cpf = customer.get("CPF") or customer.get("cpf") or customer.get("cpf_cnpj")
    cnpj = customer.get("cnpj")
    doc = cpf or cnpj

    return {
        "email": customer.get("email"),
        "name": customer.get("full_name") or customer.get("first_name") or customer.get("name"),
        "cpf_cnpj": doc,
        "customer_id": customer.get("id"),
        "phone": customer.get("mobile") or customer.get("phone"),
    }


def _extract_transaction_data(order: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados da transação do order Kiwify."""
    subscription = order.get("Subscription") or order.get("subscription") or {}
    product = order.get("Product") or order.get("product") or {}
    commissions = order.get("Commissions") or order.get("commissions") or {}

    if not isinstance(subscription, dict):
        subscription = {}
    if not isinstance(product, dict):
        product = {}
    if not isinstance(commissions, dict):
        commissions = {}

    # Dates
    next_payment = subscription.get("next_payment")
    start_date = subscription.get("start_date")

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

    # Subscription status
    sub_status = subscription.get("status")

    # Plan info
    plan = subscription.get("plan") or {}
    plan_name = plan.get("name") if isinstance(plan, dict) else None
    plan_frequency = plan.get("frequency") if isinstance(plan, dict) else None

    return {
        "transaction_id": order.get("order_id"),
        "order_ref": order.get("order_ref"),
        "subscription_id": order.get("subscription_id"),
        "amount": commissions.get("my_commission"),
        "status": order.get("order_status"),
        "subscription_status": sub_status,
        "payment_status": order.get("order_status"),
        "payment_method": order.get("payment_method"),
        "due_date": due_date,
        "recurrence_period": None,  # Kiwify não envia; inferir do plan.frequency
        "paid_at": paid_at,
        "offer_name": plan_name or product.get("product_name") or product.get("name"),
        "product_id": product.get("product_id") or product.get("id"),
        "plan_frequency": plan_frequency,
    }


def _get_allowed_products() -> Set[str]:
    raw = settings.KIWIFY_SUBSCRIPTION_PRODUCT_IDS or ""
    if not raw:
        return set()
    return {p.strip() for p in raw.split(",") if p.strip()}


def _product_allowed(product_id: Optional[str]) -> bool:
    allowed = _get_allowed_products()
    if not allowed:
        return True  # Se não há filtro, aceita todos
    if not product_id:
        return False
    return product_id in allowed


@router.post("/webhook")
async def kiwify_webhook(request: Request, db: Session = Depends(get_db)):
    """Webhook da Kiwify para processar eventos de assinatura."""
    try:
        logger.info("Webhook Kiwify recebido")

        body = await request.body()
        try:
            raw_payload = await request.json()
        except Exception as json_err:
            logger.error(f"Erro ao decodificar JSON do webhook Kiwify: {str(json_err)}")
            logger.error(f"Corpo recebido: {body.decode('utf-8', errors='replace')}")
            return {"status": "error", "reason": "invalid_json"}

        # Validação: Kiwify envia "signature" (HMAC do payload usando o token como chave).
        # O signature NÃO é o token literal — é um hash gerado.
        # Validamos que o signature está presente (confirma que veio da Kiwify).
        # Para HMAC completo, precisaríamos da public key via API /webhook-public-keys.
        signature = raw_payload.get("signature")
        if not signature:
            logger.warning("Webhook Kiwify sem signature — rejeitando")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Webhook não autorizado: signature ausente",
            )
        logger.info(f"Webhook Kiwify signature presente: {signature[:12]}...")

        # Extrair order (payload real está aninhado em "order")
        order = _extract_order(raw_payload)

        event = _extract_event(order)
        email = _extract_email(order)
        action = _map_event_to_action(event, order)

        logger.info(f"Kiwify webhook - Evento: {event}, Email: {email}, Ação: {action}")

        if not email:
            logger.warning(f"Email não encontrado no payload Kiwify. Evento: {event}")
            return {"status": "ignored", "reason": "email_not_found"}

        if action is None:
            logger.info(f"Evento Kiwify '{event}' ignorado (log only ou não mapeado)")
            return {"status": "ignored", "reason": "event_not_mapped", "event": event}

        # Extrair dados
        try:
            customer_data = _extract_customer_data(order)
            transaction_data = _extract_transaction_data(order)
        except Exception as extract_err:
            logger.error(f"Erro ao extrair dados do payload Kiwify: {str(extract_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Erro na extração de dados",
            )

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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao criar usuário",
            )

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
                provider_status=transaction_data.get("subscription_status") or transaction_data.get("status"),
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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=str(sub_err),
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ERRO CRÍTICO NO WEBHOOK KIWIFY: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"Erro interno: {str(e)}"},
        )
