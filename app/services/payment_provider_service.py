"""
Adapter/Facade para serviços de pagamento.

Delega para cakto_service ou kiwify_service conforme o feature flag ativo.
"""

import logging
from typing import Dict, List, Optional, Tuple

from app.core.config import settings
from app.core.feature_flags import get_payment_provider, is_kiwify
from app.schemas.subscription import PlanInfo

logger = logging.getLogger(__name__)


class PaymentProviderError(RuntimeError):
    pass


def check_active_subscription(email: str) -> Tuple[bool, Optional[str]]:
    """Verifica assinatura ativa delegando para o provider correto."""
    if is_kiwify():
        from app.services.kiwify_service import check_active_subscription as kiwify_check, KiwifyError
        try:
            return kiwify_check(email)
        except KiwifyError as e:
            logger.error(f"Kiwify check_active_subscription error: {e}")
            raise PaymentProviderError(str(e))
    else:
        from app.services.cakto_service import check_active_subscription as cakto_check, CaktoError
        try:
            return cakto_check(email)
        except CaktoError as e:
            logger.error(f"Cakto check_active_subscription error: {e}")
            raise PaymentProviderError(str(e))


def get_checkout_url(
    plan_id: str = "mensal",
    email: str = None,
    name: str = None,
    cpf_cnpj: str = None,
) -> str:
    """Retorna URL de checkout do provider ativo."""
    if is_kiwify():
        from app.services.kiwify_service import create_checkout_url
        return create_checkout_url(plan_id=plan_id)
    else:
        from app.services.cakto_service import create_checkout_url
        return create_checkout_url(email=email, name=name, cpf_cnpj=cpf_cnpj, plan_id=plan_id)


def get_plans() -> List[PlanInfo]:
    """Retorna lista de planos do provider ativo."""
    provider = get_payment_provider()

    if provider == "kiwify":
        plans_dict = settings.KIWIFY_PLANS
    else:
        plans_dict = settings.CAKTO_PLANS

    return [
        PlanInfo(
            id=plan_id,
            name=plan_data["name"],
            checkout_url=plan_data["checkout_url"],
            period=plan_data["period"],
        )
        for plan_id, plan_data in plans_dict.items()
    ]


def get_customer_by_email(email: str) -> Optional[Dict]:
    """Busca dados do cliente no provider ativo."""
    if is_kiwify():
        from app.services.kiwify_service import get_customer_by_email as kiwify_get
        return kiwify_get(email)
    else:
        from app.services.cakto_service import get_customer_by_email as cakto_get
        return cakto_get(email)
