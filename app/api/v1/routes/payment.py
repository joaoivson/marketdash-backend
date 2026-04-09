"""
Rotas genéricas de pagamento.

Delegam para o payment_provider_service conforme o feature flag ativo.
Os webhooks específicos (/cakto/webhook e /kiwify/webhook) permanecem
registrados separadamente pois são URLs fixas para os providers externos.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.subscription import PlansResponse
from app.services.payment_provider_service import get_plans, get_checkout_url

logger = logging.getLogger(__name__)

router = APIRouter(tags=["payment"])


@router.get("/plans", response_model=PlansResponse)
def payment_plans():
    """Retorna planos do provider ativo (Cakto ou Kiwify)."""
    plans = get_plans()
    return PlansResponse(plans=plans)


@router.get("/checkout-url")
def payment_checkout_url(
    email: str = Query(None, description="Email do usuário (opcional)"),
    name: str = Query(None, description="Nome do usuário"),
    cpf_cnpj: str = Query(None, description="CPF/CNPJ do usuário"),
    plan: str = Query("mensal", description="ID do plano"),
):
    """Retorna URL de checkout do provider ativo."""
    try:
        url = get_checkout_url(plan_id=plan, email=email, name=name, cpf_cnpj=cpf_cnpj)
        return {"checkout_url": url}
    except Exception as e:
        logger.error(f"Erro ao gerar checkout URL: {e}")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
