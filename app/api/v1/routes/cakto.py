from typing import Any, Dict, Optional, Set, List
from datetime import datetime, timedelta, timezone
import logging
import traceback
from fastapi import APIRouter, Depends, HTTPException, Request, Query, status
from fastapi.responses import JSONResponse
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
from app.schemas.subscription import PlansResponse, PlanInfo

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
        "cpf_cnpj": _sanitize_document(
            customer.get("docNumber")
            or customer.get("cpf_cnpj")
            or data.get("cpf_cnpj")
        ),
        "customer_id": customer.get("id") or data.get("customer_id"),
        "phone": customer.get("phone") or data.get("phone"),
    }


def _parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed_input = text.replace("Z", "+00:00") if text.endswith("Z") else text
        try:
            return datetime.fromisoformat(parsed_input)
        except ValueError:
            # Tentar substituir espaço por 'T' em alguns formatos
            try:
                return datetime.fromisoformat(parsed_input.replace(" ", "T"))
            except ValueError:
                logger.warning(f"Não foi possível converter data Cakto: {text}")
                return None
    return None


def _extract_transaction_data(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extrai dados da transação do payload do webhook."""
    try:
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

        offer = data.get("offer") if isinstance(data.get("offer"), dict) else {}
        product = data.get("product") if isinstance(data.get("product"), dict) else {}
        subscription = data.get("subscription") if isinstance(data.get("subscription"), dict) else {}

        due_date_raw = (
            data.get("due_date")
            or subscription.get("next_payment_date")
            or subscription.get("due_date")
            or data.get("paidAt")
        )

        offer_name = None
        if isinstance(offer, dict):
            offer_name = offer.get("name")
        if not offer_name and isinstance(product, dict):
            offer_name = product.get("name")

        status = data.get("status")
        subscription_status = subscription.get("status") if isinstance(subscription, dict) else None
        payment_status = data.get("payment_status") or subscription.get("payment_status") if isinstance(subscription, dict) else None

        return {
            "transaction_id": data.get("id") or data.get("transaction_id"),
            "amount": data.get("amount"),
            "status": status,
            "subscription_status": subscription_status,
            "payment_status": payment_status,
            "payment_method": data.get("paymentMethod") or data.get("payment_method"),
            "due_date": _parse_datetime(due_date_raw),
            "due_date_present": due_date_raw is not None,
            "offer_name": offer_name,
        }
    except Exception as e:
        logger.error(f"Erro ao extrair transaction_data: {str(e)}")
        return {
            "transaction_id": None,
            "amount": 0,
            "status": None,
            "subscription_status": None,
            "payment_status": None,
            "payment_method": None,
            "due_date": None,
            "due_date_present": False,
            "offer_name": None,
        }


def _sanitize_document(value: Any) -> Optional[str]:
    if value is None:
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    return digits or None


def _get_allowed_products() -> Set[str]:
    raw = settings.CAKTO_SUBSCRIPTION_PRODUCT_IDS or ""
    if not raw:
        return set()
    normalized = raw.replace("[", "").replace("]", "")
    allowed = set()
    for piece in normalized.split(","):
        cleaned = piece.strip().strip("'\"")
        if cleaned:
            allowed.add(cleaned)
    return allowed


def _extract_product_id(payload: Dict[str, Any]) -> Optional[str]:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload

    def _add_candidate(value: Any, bucket: List[str]) -> None:
        if value is None:
            return
        text = str(value).strip()
        if not text:
            return
        bucket.append(text)

    candidates: List[str] = []
    for key in ("product_id", "offer_product_id", "product_code"):
        _add_candidate(data.get(key), candidates)

    product = data.get("product") or {}
    if isinstance(product, dict):
        _add_candidate(product.get("id"), candidates)

    offer = data.get("offer") or {}
    offer_id = None
    if isinstance(offer, dict):
        offer_id = offer.get("id")
        _add_candidate(offer_id, candidates)
        _add_candidate(offer.get("product_id"), candidates)
        offer_product = offer.get("product") or {}
        if isinstance(offer_product, dict):
            _add_candidate(offer_product.get("id"), candidates)

    checkout = data.get("checkout") or data.get("checkout_id")
    if offer_id and checkout:
        _add_candidate(f"{offer_id}_{checkout}", candidates)
    if isinstance(product, dict) and product.get("id") and checkout:
        _add_candidate(f"{product.get('id')}_{checkout}", candidates)

    seen = set()
    deduped: List[str] = []
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped.append(candidate)

    allowed = _get_allowed_products()
    if allowed:
        for candidate in deduped:
            for allowed_id in allowed:
                # Se houver match exato ou parcial (um contendo o outro)
                if (
                    candidate == allowed_id
                    or (len(candidate) >= 5 and allowed_id.startswith(candidate))
                    or (len(allowed_id) >= 5 and candidate.startswith(allowed_id))
                ):
                    logger.info(f"Produto identificado: {allowed_id} (match com {candidate})")
                    return allowed_id

    return deduped[0] if deduped else None


def _product_allowed(product_id: Optional[str]) -> bool:
    allowed = _get_allowed_products()
    if not allowed:
        return True
    if not product_id:
        return False
        
    for allowed_id in allowed:
        if (
            product_id == allowed_id
            or (len(product_id) >= 5 and allowed_id.startswith(product_id))
            or (len(allowed_id) >= 5 and product_id.startswith(allowed_id))
        ):
            return True
    return False


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
    try:
        logger.info("Webhook Cakto recebido")
        
        # Obter payload bruto primeiro para log em caso de erro de JSON
        body = await request.body()
        try:
            payload = await request.json()
        except Exception as json_err:
            logger.error(f"Erro ao decodificar JSON do webhook: {str(json_err)}")
            logger.error(f"Corpo recebido: {body.decode('utf-8', errors='replace')}")
            return {"status": "error", "reason": "invalid_json"}

        # Validação do secret
        if settings.CAKTO_WEBHOOK_SECRET:
            secret = (
                request.headers.get("x-cakto-secret")
                or request.headers.get("x-webhook-secret")
                or request.headers.get("x-cakto-signature")
            )
            
            payload_secret = payload.get("secret")
            if secret != settings.CAKTO_WEBHOOK_SECRET and payload_secret != settings.CAKTO_WEBHOOK_SECRET:
                logger.warning(f"Webhook não autorizado. Secret esperado: {settings.CAKTO_WEBHOOK_SECRET[:10]}...")
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Webhook não autorizado")

        event = _extract_event(payload)
        email = _extract_email(payload)
        product_id = _extract_product_id(payload)
        action = _infer_action(payload, event)

        logger.info(f"Webhook processando - Evento: {event}, Email: {email}, Ação: {action}, Produto: {product_id}")

        if not email:
            logger.warning(f"Email não encontrado no payload. Evento: {event}")
            return {"status": "ignored", "reason": "email_not_found"}
            
        if not _product_allowed(product_id):
            logger.warning(f"Produto não autorizado: {product_id}. IDs permitidos: {settings.CAKTO_SUBSCRIPTION_PRODUCT_IDS}")
            return {"status": "ignored", "reason": "product_not_allowed", "product_id": product_id}
            
        if not action:
            logger.info(f"Evento {event} ignorado (nenhuma ação ativa/desativa mapeada)")
            return {"status": "ignored", "reason": "event_not_mapped", "event": event}

        # Extrair dados do cliente e transação com segurança
        try:
            customer_data = _extract_customer_data(payload)
            transaction_data = _extract_transaction_data(payload)
        except Exception as extract_err:
            logger.error(f"Erro ao extrair dados do payload Cakto: {str(extract_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Erro na extração de dados")

        # Buscar ou criar usuário
        user_repo = UserRepository(db)
        user = user_repo.get_by_email(email)
        
        if not user:
            # Tentar buscar por CPF/CNPJ se disponível
            doc_id = customer_data.get("cpf_cnpj")
            if doc_id:
                user = user_repo.get_by_cpf(doc_id)
                if user:
                    logger.info(f"Usuário ID {user.id} encontrado por documento. Atualizando email para {email}")
                    user.email = email
                    db.commit()
                    db.refresh(user)
        
        user_created = False
        user_has_password = False
        
        if not user:
            # Criar novo usuário
            logger.info(f"Criando novo usuário do Cakto: {email}")
            auth_service = AuthService(user_repo)
            try:
                user = auth_service.register_from_cakto(
                    email=customer_data["email"],
                    name=customer_data["name"],
                    cpf_cnpj=customer_data["cpf_cnpj"]
                )
                user_created = True
                user_has_password = False
                logger.info(f"Usuário {email} criado com ID {user.id}")
            except Exception as reg_err:
                logger.error(f"Erro ao registrar usuário via webhook: {str(reg_err)}")
                logger.error(traceback.format_exc())
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao criar usuário")
        else:
            # Usuário existente: verificar se já tem senha
            user_has_password = not bool(user.password_set_token)
            logger.info(f"Usuário {user.id} já existe. Tem senha: {user_has_password}")

        # Atualizar assinatura
        subscription_service = SubscriptionService(SubscriptionRepository(db))
        
        # Calcular expiração
        expires_at = transaction_data.get("due_date")
        if not expires_at and action == "activate":
            expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            
        try:
            logger.info(f"Atualizando assinatura para usuário {user.id} (ação: {action})")
            subscription = subscription_service.set_active(
                user_id=user.id,
                plan="marketdash" if action == "activate" else "free",
                is_active=(action == "activate"),
                cakto_customer_id=customer_data.get("customer_id"),
                cakto_transaction_id=transaction_data.get("transaction_id"),
                expires_at=expires_at,
                cakto_status=transaction_data.get("subscription_status") or transaction_data.get("status"),
                cakto_offer_name=transaction_data.get("offer_name"),
                cakto_due_date=transaction_data.get("due_date"),
                cakto_subscription_status=transaction_data.get("subscription_status"),
                cakto_payment_status=transaction_data.get("payment_status"),
                cakto_payment_method=transaction_data.get("payment_method"),
            )
            
            # Commit final do status da assinatura
            if action == "activate":
                subscription.last_validation_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(subscription)
                
                # --- Lógica de E-mail (Totalmente Isolada) ---
                try:
                    from app.services.email_service import EmailService
                    email_service = EmailService()
                    user_name = customer_data.get("name") or user.name or "Usuário"
                    
                    if user_created:
                        logger.info(f"Usuário novo: Email de boas-vindas já deve ter sido enviado via register_from_cakto")
                    else:
                        if user_has_password:
                            logger.info(f"Enviando e-mail de reativação para {email}")
                            email_service.send_welcome_back_email(user_email=email, user_name=user_name)
                        else:
                            logger.info(f"Usuário sem senha: Enviando link de definição para {email}")
                            token = user.password_set_token
                            if not token:
                                import secrets
                                import string
                                token = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
                                user.password_set_token = token
                                user.password_set_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
                                db.commit()
                            email_service.send_set_password_email(user_email=email, user_name=user_name, token=token)
                except Exception as email_err:
                    logger.error(f"FALHA NO ENVIO DE E-MAIL: {str(email_err)}")
                    # Não relançamos o erro de e-mail para não falhar o webhook

            logger.info(f"Webhook processado com sucesso para {email}")
            return {
                "status": "ok",
                "action": action,
                "user_id": user.id,
                "subscription_active": subscription.is_active,
                "next_payment_date": subscription.cakto_due_date.isoformat() if subscription.cakto_due_date else None,
                "user_created": user_created
            }

        except Exception as sub_err:
            logger.error(f"Erro no processamento da assinatura: {str(sub_err)}")
            logger.error(traceback.format_exc())
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(sub_err))

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"ERRO CRÍTICO NÃO TRATADO NO WEBHOOK: {str(e)}")
        logger.error(traceback.format_exc())
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": f"Erro interno: {str(e)}"}
        )


@router.get("/plans", response_model=PlansResponse)
def get_plans():
    """
    Retorna lista de todos os planos de assinatura disponíveis.
    
    Permite que o frontend exiba as opções de planos para o usuário escolher.
    """
    all_plans = settings.get_all_cakto_plans()
    
    plans_list = [
        PlanInfo(
            id=plan_id,
            name=plan_data["name"],
            checkout_url=plan_data["checkout_url"],
            period=plan_data["period"]
        )
        for plan_id, plan_data in all_plans.items()
    ]
    
    return PlansResponse(plans=plans_list)


@router.get("/checkout-url")
def get_checkout_url(
    email: str = Query(None, description="Email do usuário (opcional)"),
    name: str = Query(None, description="Nome do usuário"),
    cpf_cnpj: str = Query(None, description="CPF/CNPJ do usuário"),
    plan: str = Query("principal", description="ID do plano (principal, trimestral, anual)"),
):
    """
    Gera URL de checkout do Cakto com parâmetros pré-preenchidos.
    
    Args:
        email: Email do usuário (opcional - pode ser omitido para usuários não logados)
        name: Nome do usuário (opcional)
        cpf_cnpj: CPF/CNPJ do usuário (opcional)
        plan: ID do plano desejado. Valores aceitos: "principal", "trimestral", "anual". Default: "principal"
    
    Returns:
        URL de checkout do Cakto para o plano especificado
    """
    # Validar se o plano existe
    if plan not in settings.CAKTO_PLANS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Plano '{plan}' não encontrado. Planos disponíveis: {', '.join(settings.CAKTO_PLANS.keys())}"
        )
    
    checkout_url = create_checkout_url(
        email=email, 
        name=name, 
        cpf_cnpj=cpf_cnpj,
        plan_id=plan
    )
    return {"checkout_url": checkout_url}
