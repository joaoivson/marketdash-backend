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

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService, UNSET
from app.services.webhook_helpers import (
    find_or_create_user,
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
    "order_refunded",
    "order_chargedback",
    "chargeback",
}

LATE_EVENTS: Set[str] = {
    "subscription_late",
}

# Cortam o acesso na hora — não existe "período já pago" a honrar
REVOKE_NOW_EVENTS: Set[str] = {
    "order_refunded",
    "order_chargedback",
    "chargeback",
    "compra_reembolsada",
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
    """Mapeia evento para ação (activate/deactivate/late/None)."""
    if event in ACTIVATE_EVENTS:
        return "activate"
    if event in LATE_EVENTS:
        return "late"
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


def _plan_lookup_keys(order: Dict[str, Any]) -> list:
    """
    Chaves candidatas para identificar o plano, da mais específica pra menos.

    `Product.product_id` NÃO serve sozinho: na Kiwify existe um único produto
    ("MarketDash") e todos os planos compartilham o mesmo UUID. Quem distingue
    Essencial/Pro e mensal/trimestral/anual é o slug do checkout — que vem em
    `checkout_link` e é exatamente como kiwify_plan_products está chaveada.
    """
    product = order.get("Product") or order.get("product") or {}
    if not isinstance(product, dict):
        product = {}
    subscription = order.get("Subscription") or order.get("subscription") or {}
    if not isinstance(subscription, dict):
        subscription = {}
    plan = subscription.get("plan") if isinstance(subscription.get("plan"), dict) else {}

    brutos = [
        order.get("checkout_link"),
        plan.get("id"),
        product.get("product_id") or product.get("id"),
        order.get("product_id"),
    ]

    chaves = []
    for bruto in brutos:
        if not bruto:
            continue
        texto = str(bruto).strip()
        if not texto:
            continue
        # Pode vir como URL completa do checkout
        slug = texto.rstrip("/").split("/")[-1]
        for candidato in (texto, slug):
            if candidato and candidato not in chaves:
                chaves.append(candidato)
    return chaves


def _lookup_plan_product(db: Session, keys: list):
    """Busca mapeamento (plano, periodo) testando as chaves candidatas em ordem."""
    if not keys:
        return None
    from app.models.kiwify_plan_product import KiwifyPlanProduct

    for key in keys:
        row = (
            db.query(KiwifyPlanProduct)
            .filter(KiwifyPlanProduct.product_id == key)
            .first()
        )
        if row:
            return row
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


def _extract_customer_access(order: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extrai `Subscription.customer_access` — a fonte de verdade da Kiwify sobre acesso.

    No cancelamento a Kiwify manda `has_access: true` + `access_until` no fim do
    período já pago. Sem isso, o cancelamento cortava o acesso na hora.
    """
    subscription = order.get("Subscription") or order.get("subscription") or {}
    if not isinstance(subscription, dict):
        subscription = {}
    access = subscription.get("customer_access") or {}
    if not isinstance(access, dict):
        access = {}

    has_access = access.get("has_access")
    if has_access is not None:
        has_access = bool(has_access)

    raw_until = access.get("access_until") or subscription.get("access_until")
    access_until = None
    if raw_until:
        try:
            access_until = datetime.fromisoformat(str(raw_until).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            logger.warning("Kiwify access_until não parseável: %r", raw_until)

    if access_until is not None and access_until.tzinfo is None:
        access_until = access_until.replace(tzinfo=timezone.utc)

    return {"has_access": has_access, "access_until": access_until}


def _resolve_keep_access_until(
    event: str,
    access: Dict[str, Any],
    fallback_due_date: Optional[datetime],
) -> Optional[datetime]:
    """
    Até quando o acesso continua valendo num evento de desativação.

    None = corta agora. Reembolso/chargeback sempre corta; cancelamento honra
    o período já pago (`access_until`, ou o `next_payment` como fallback).
    """
    if event in REVOKE_NOW_EVENTS:
        return None
    if access.get("has_access") is False:
        return None
    return access.get("access_until") or fallback_due_date


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


def _link_event_to_user(db: Session, event_id: Optional[int], user_id: int) -> None:
    """
    Amarra o evento recém-gravado ao usuário.

    O histórico é gravado ANTES de criar/achar o usuário (pra não perder webhook se
    a criação falhar), então na primeira compra o evento nasce com user_id NULL —
    e tudo que agrupa por user_id perdia esse cliente. Aqui fechamos a ponta.
    """
    if not event_id:
        return
    try:
        from app.models.subscription_event import SubscriptionEvent

        row = db.query(SubscriptionEvent).filter(SubscriptionEvent.id == event_id).first()
        if row is not None and row.user_id is None:
            row.user_id = user_id
            db.commit()
    except Exception as link_err:  # noqa: BLE001
        logger.error("Falha ao vincular subscription_event %s ao user %s: %s", event_id, user_id, link_err)
        try:
            db.rollback()
        except Exception:
            pass


def _send_email_background(
    user_id: int,
    user_email: str,
    user_created: bool,
    user_has_password: bool,
    customer_name: Optional[str],
    has_password_set_token: bool,
    password_set_token: Optional[str],
):
    """Envia email em background thread (após responder 200 à Kiwify)."""
    try:
        from app.services.email_service import EmailService
        email_service = EmailService()
        user_name = customer_name or "Usuário"

        if user_created:
            logger.info(f"[BG] Usuário novo {user_email}: email de boas-vindas já enviado via register_from_webhook")
        elif user_has_password:
            logger.info(f"[BG] Enviando email de reativação para {user_email}")
            email_service.send_welcome_back_email(user_email=user_email, user_name=user_name)
        else:
            logger.info(f"[BG] Enviando link de definição de senha para {user_email}")
            if password_set_token:
                email_service.send_set_password_email(
                    user_email=user_email, user_name=user_name, token=password_set_token
                )
    except Exception as e:
        logger.error(f"[BG] FALHA NO ENVIO DE E-MAIL para {user_email}: {str(e)}")


@router.post("/webhook")
async def kiwify_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
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

        # Log de autenticação (signature pode ou não estar presente dependendo do evento)
        signature = raw_payload.get("signature")
        if signature:
            logger.info(f"Webhook Kiwify signature presente: {signature[:12]}...")
        else:
            logger.info("Webhook Kiwify sem signature (aceito — URL é secreta)")

        # Extrair order (payload real está aninhado em "order")
        order = _extract_order(raw_payload)

        event = _extract_event(order)
        email = _extract_email(order)
        action = _map_event_to_action(event, order)

        logger.info(f"Kiwify webhook - Evento: {event}, Email: {email}, Ação: {action}")

        # Histórico append-only — SEMPRE grava (mesmo evento desconhecido / sem e-mail).
        # Não altera regras de activate/deactivate abaixo.
        recorded_event_id: Optional[int] = None
        try:
            from app.services.subscription_event_recorder import record_subscription_event

            recorded = record_subscription_event(
                db,
                raw_payload if isinstance(raw_payload, dict) else {"order": order},
                event or "unknown",
            )
            db.commit()
            if recorded is not None:
                recorded_event_id = recorded.id
        except Exception as hist_err:  # noqa: BLE001
            logger.error("Falha ao gravar subscription_event (seguindo webhook): %s", hist_err)
            try:
                db.rollback()
            except Exception:
                pass

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

        # Verificar filtro legado de produto (env) — se vazio, aceita todos.
        # Testa todas as chaves: o env pode ter sido preenchido com slug de
        # checkout ou com o UUID do produto.
        product_id = transaction_data.get("product_id")
        plan_keys = _plan_lookup_keys(order)
        if not any(_product_allowed(k) for k in (plan_keys or [product_id])):
            logger.warning(
                "Produto Kiwify não autorizado (env filter). Chaves: %s", plan_keys
            )
            return {"status": "ignored", "reason": "product_not_allowed", "product_id": product_id}

        # Mapeamento → (plano, periodo)
        plan_product = _lookup_plan_product(db, plan_keys)
        plano_indefinido = action == "activate" and not plan_product
        if plano_indefinido:
            # NUNCA descartar uma compra paga por causa disso. Antes o webhook
            # retornava aqui, ANTES de criar o usuário — o cliente pagava, o evento
            # ficava gravado (painel admin mostrava a ativação) e ele nunca recebia
            # o e-mail de acesso. Agora seguimos: cria o usuário, manda o e-mail e
            # libera no menor plano; o alerta abaixo diz o que cadastrar pra acertar
            # o tier.
            logger.error(
                "ALERTA: plano Kiwify não mapeado para %s. Chaves tentadas: %s. "
                "Liberando acesso no plano mínimo — cadastre em kiwify_plan_products "
                "e reenvie o webhook para corrigir o tier.",
                email,
                plan_keys,
            )

        # Buscar ou criar usuário
        try:
            user, user_created, user_has_password = find_or_create_user(email, customer_data, db)
            if user_created:
                logger.info(f"Usuário {email} criado via webhook Kiwify com ID {user.id}")
            _link_event_to_user(db, recorded_event_id, user.id)
        except Exception as reg_err:
            logger.error(f"Erro ao registrar usuário via webhook Kiwify: {str(reg_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao criar usuário",
            )

        # Calcular expiração
        expires_at = transaction_data.get("due_date")
        customer_access = _extract_customer_access(order)
        keep_access_until: Optional[datetime] = None

        if action == "activate":
            expires_at = calculate_expires_at(
                expires_at,
                transaction_data.get("recurrence_period"),
                transaction_data.get("paid_at"),
            )
        elif action == "deactivate":
            # Cancelamento não corta acesso na hora: vale até o fim do período pago
            keep_access_until = _resolve_keep_access_until(event, customer_access, expires_at)
            if keep_access_until is not None:
                expires_at = keep_access_until
            logger.info(
                "Kiwify deactivate user=%s evento=%s has_access=%s acesso_ate=%s",
                email,
                event,
                customer_access.get("has_access"),
                keep_access_until.isoformat() if keep_access_until else None,
            )

        subscription_service = SubscriptionService(SubscriptionRepository(db))

        try:
            if action == "late":
                current = subscription_service.repo.get_by_user_id(user.id)
                if current:
                    current.assinatura_status = "inadimplente"
                    db.commit()
                    db.refresh(current)
                logger.info(f"Kiwify late payment marcado para {email}")
                return {
                    "status": "ok",
                    "action": "late",
                    "user_id": user.id,
                    "assinatura_status": "inadimplente",
                }

            if action == "activate" and plan_product:
                plan_id = plan_product.plano
                periodo = plan_product.periodo
            elif action == "activate":
                # Plano não mapeado: preserva o que o usuário já tinha; se for
                # cliente novo, entra no mínimo. Melhor tier errado que sem acesso.
                current = subscription_service.repo.get_by_user_id(user.id)
                plan_id = (current.plan if current and current.plan else None) or "essencial"
                periodo = (current.plano_periodo if current else None) or (
                    transaction_data.get("plan_frequency") or None
                )
            else:
                plan_id = "essencial"
                periodo = None
                current = subscription_service.repo.get_by_user_id(user.id)
                if current and current.plano_periodo:
                    periodo = current.plano_periodo

            logger.info(
                "Atualizando assinatura Kiwify user=%s action=%s plan=%s periodo=%s",
                user.id, action, plan_id, periodo,
            )
            subscription = subscription_service.set_active(
                user_id=user.id,
                plan=plan_id,
                is_active=(action == "activate"),
                expires_at=expires_at,
                plano_periodo=periodo,
                assinatura_status="ativa" if action == "activate" else "cancelada",
                assinatura_vence_em=expires_at,
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
                keep_access_until=keep_access_until if action == "deactivate" else UNSET,
            )

            # O webhook é a validação mais recente que temos do provider — vale para
            # qualquer ação, senão o usuário cai na revalidação síncrona a cada request.
            subscription.last_validation_at = datetime.now(timezone.utc)
            db.commit()
            db.refresh(subscription)

            if action == "activate":
                db.refresh(user)

                background_tasks.add_task(
                    _send_email_background,
                    user_id=user.id,
                    user_email=email,
                    user_created=user_created,
                    user_has_password=user_has_password,
                    customer_name=customer_data.get("name"),
                    has_password_set_token=bool(user.password_set_token),
                    password_set_token=user.password_set_token,
                )

            logger.info(f"Webhook Kiwify processado com sucesso para {email}")
            return {
                "status": "ok",
                "action": action,
                "user_id": user.id,
                "plan": subscription.plan,
                "plano_periodo": subscription.plano_periodo,
                "subscription_active": subscription.is_active,
                "plano_mapeado": not plano_indefinido,
                "acesso_ate": keep_access_until.isoformat() if keep_access_until else None,
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
