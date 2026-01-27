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
        expires_at: Optional[datetime] = None
    ):
        """Atualiza ou cria subscription com dados do Cakto."""
        subscription = self.repo.upsert(
            user_id=user_id, 
            plan=plan, 
            is_active=is_active,
            cakto_customer_id=cakto_customer_id,
            cakto_transaction_id=cakto_transaction_id,
            expires_at=expires_at
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
            subscription.is_active = has_access
            subscription.last_validation_at = datetime.now(timezone.utc)
            
            if has_access:
                subscription.plan = "marketdash"
                # Se não tem expires_at, definir para 30 dias a partir de agora
                if not subscription.expires_at:
                    subscription.expires_at = datetime.now(timezone.utc) + timedelta(days=30)
            else:
                subscription.plan = "free"
            
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
