from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from app.repositories.subscription_repository import SubscriptionRepository
from app.services.cakto_service import check_active_subscription, get_subscription_status, CaktoError
import logging

logger = logging.getLogger(__name__)


class SubscriptionService:
    def __init__(self, repo: SubscriptionRepository):
        self.repo = repo

    def set_active(
        self, 
        user_id: int, 
        plan: str, 
        is_active: bool,
        cakto_customer_id: Optional[str] = None,
        cakto_transaction_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        cakto_status: Optional[str] = None,
        cakto_offer_name: Optional[str] = None,
        cakto_due_date: Optional[datetime] = None,
        cakto_subscription_status: Optional[str] = None,
        cakto_payment_status: Optional[str] = None,
        cakto_payment_method: Optional[str] = None,
    ):
        """Atualiza ou cria subscription com dados do Cakto."""
        normalized_offer_name = None
        if isinstance(cakto_offer_name, str):
            stripped_offer = cakto_offer_name.strip()
            normalized_offer_name = stripped_offer or None
        
        normalized_plan = None
        if isinstance(plan, str):
            stripped_plan = plan.strip()
            normalized_plan = stripped_plan or None
        
        plan_value = normalized_offer_name or normalized_plan or "free"

        normalized_due_date = cakto_due_date  # Due date pode ser None e deve refletir diretamente em expires_at

        normalized_status = None
        if isinstance(cakto_status, str):
            stripped_status = cakto_status.strip()
            if stripped_status:
                normalized_status = stripped_status.lower()
                cakto_status = stripped_status
            else:
                cakto_status = None
        
        normalized_subscription_status = None
        if isinstance(cakto_subscription_status, str):
            stripped_sub_status = cakto_subscription_status.strip()
            normalized_subscription_status = stripped_sub_status or None
        else:
            normalized_subscription_status = cakto_subscription_status

        normalized_payment_status = None
        if isinstance(cakto_payment_status, str):
            stripped_payment_status = cakto_payment_status.strip()
            normalized_payment_status = stripped_payment_status or None
        else:
            normalized_payment_status = cakto_payment_status

        normalized_payment_method = None
        if isinstance(cakto_payment_method, str):
            stripped_payment_method = cakto_payment_method.strip()
            normalized_payment_method = stripped_payment_method or None
        else:
            normalized_payment_method = cakto_payment_method

        is_active_value = is_active
        if normalized_status is not None:
            is_active_value = normalized_status == "active"

        subscription = self.repo.upsert(
            user_id=user_id, 
            plan=plan_value, 
            is_active=is_active_value,
            cakto_customer_id=cakto_customer_id,
            cakto_transaction_id=cakto_transaction_id,
            expires_at=normalized_due_date,
            cakto_status=cakto_status,
            cakto_offer_name=normalized_offer_name,
            cakto_due_date=normalized_due_date,
            cakto_subscription_status=normalized_subscription_status,
            cakto_payment_status=normalized_payment_status,
            cakto_payment_method=normalized_payment_method,
        )
        return subscription

    def needs_validation(self, user_id: int) -> bool:
        """Verifica se precisa validar assinatura (passou mais de 30 dias)."""
        subscription = self.repo.get_by_user_id(user_id)
        if not subscription:
            return True  # Se não tem subscription, precisa validar
        
        if not subscription.last_validation_at:
            return True  # Nunca validou
        
        # Verificar se passou mais de 30 dias
        days_since_validation = (datetime.now(timezone.utc) - subscription.last_validation_at).days
        return days_since_validation >= 30

    def check_and_update_subscription(self, user_id: int, user_email: str) -> bool:
        """Valida assinatura com Cakto e atualiza no banco. Retorna True se ativa."""
        try:
            has_access, reason = check_active_subscription(user_email)
            
            subscription = self.repo.get_by_user_id(user_id)
            if not subscription:
                # Criar subscription se não existe
                subscription = self.repo.upsert(
                    user_id=user_id,
                    plan="free",
                    is_active=False
                )
            
            # Atualizar status e data de validação
            if subscription.cakto_status:
                subscription.is_active = subscription.cakto_status.strip().lower() == "active"
            else:
                subscription.is_active = has_access
            subscription.last_validation_at = datetime.now(timezone.utc)
            
            if subscription.cakto_offer_name:
                subscription.plan = subscription.cakto_offer_name
            elif subscription.plan:
                subscription.plan = subscription.plan
            else:
                subscription.plan = "free"

            if subscription.cakto_due_date:
                subscription.expires_at = subscription.cakto_due_date
            elif not subscription.is_active:
                subscription.expires_at = None
            
            # Fazer commit das alterações
            self.repo.db.commit()
            self.repo.db.refresh(subscription)
            
            logger.info(f"Subscription validated for user {user_id}: active={has_access}")
            return has_access
            
        except CaktoError as e:
            logger.error(f"Error validating subscription for user {user_id}: {str(e)}")
            # Em caso de erro, manter status atual mas não atualizar last_validation_at
            return False

    def get_subscription_status(self, user_id: int) -> Dict[str, Any]:
        """Retorna status completo da assinatura do usuário."""
        subscription = self.repo.get_by_user_id(user_id)
        
        if not subscription:
            return {
                "has_subscription": False,
                "is_active": False,
                "plan": "free",
                "needs_validation": True,
                "expires_at": None,
            }
        
        needs_validation = self.needs_validation(user_id)
        
        return {
            "has_subscription": True,
            "is_active": subscription.is_active,
            "plan": subscription.plan,
            "needs_validation": needs_validation,
            "last_validation_at": subscription.last_validation_at.isoformat() if subscription.last_validation_at else None,
            "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else None,
            "cakto_customer_id": subscription.cakto_customer_id,
            "cakto_status": subscription.cakto_status,
            "cakto_offer_name": subscription.cakto_offer_name,
            "cakto_due_date": subscription.cakto_due_date.isoformat() if subscription.cakto_due_date else None,
            "cakto_subscription_status": subscription.cakto_subscription_status,
            "cakto_payment_status": subscription.cakto_payment_status,
            "cakto_payment_method": subscription.cakto_payment_method,
        }
    
    def cancel_subscription(self, user_id: int) -> bool:
        """
        Cancela a assinatura do usuário.
        
        Desativa a assinatura no nosso sistema. O cancelamento real na Cakto
        deve ser feito pelo usuário na plataforma Cakto. Quando o cancelamento
        for processado pela Cakto, o webhook atualizará automaticamente.
        
        Returns:
            True se a assinatura foi cancelada, False se não havia assinatura ativa
        """
        subscription = self.repo.get_by_user_id(user_id)
        
        if not subscription:
            logger.info(f"Tentativa de cancelar assinatura para usuário {user_id} sem subscription")
            return False
        
        if not subscription.is_active:
            logger.info(f"Assinatura do usuário {user_id} já estava inativa")
            return False
        
        # Desativar assinatura e mudar para plano free
        subscription.is_active = False
        subscription.plan = "free"
        
        # Fazer commit
        self.repo.db.commit()
        self.repo.db.refresh(subscription)
        
        logger.info(f"Assinatura cancelada para usuário {user_id}")
        return True
