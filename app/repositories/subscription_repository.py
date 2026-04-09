from typing import Optional

from sqlalchemy.orm import Session

from app.models.subscription import Subscription


class SubscriptionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user_id(self, user_id: int) -> Optional[Subscription]:
        return self.db.query(Subscription).filter(Subscription.user_id == user_id).first()

    def upsert(
        self,
        user_id: int,
        plan: str,
        is_active: bool,
        cakto_customer_id: str = None,
        cakto_transaction_id: str = None,
        expires_at = None,
        cakto_status: str = None,
        cakto_offer_name: str = None,
        cakto_due_date = None,
        cakto_subscription_status: str = None,
        cakto_payment_status: str = None,
        cakto_payment_method: str = None,
        # Generic provider fields
        provider: str = None,
        provider_customer_id: str = None,
        provider_transaction_id: str = None,
        provider_status: str = None,
        provider_offer_name: str = None,
        provider_due_date = None,
        provider_subscription_status: str = None,
        provider_payment_status: str = None,
        provider_payment_method: str = None,
        provider_order_id: str = None,
    ) -> Subscription:
        try:
            subscription = self.get_by_user_id(user_id)
            if not subscription:
                subscription = Subscription(
                    user_id=user_id,
                    plan=plan,
                    is_active=is_active,
                    cakto_customer_id=cakto_customer_id,
                    cakto_transaction_id=cakto_transaction_id,
                    expires_at=expires_at,
                    cakto_status=cakto_status,
                    cakto_offer_name=cakto_offer_name,
                    cakto_due_date=cakto_due_date,
                    cakto_subscription_status=cakto_subscription_status,
                    cakto_payment_status=cakto_payment_status,
                    cakto_payment_method=cakto_payment_method,
                    provider=provider,
                    provider_customer_id=provider_customer_id,
                    provider_transaction_id=provider_transaction_id,
                    provider_status=provider_status,
                    provider_offer_name=provider_offer_name,
                    provider_due_date=provider_due_date,
                    provider_subscription_status=provider_subscription_status,
                    provider_payment_status=provider_payment_status,
                    provider_payment_method=provider_payment_method,
                    provider_order_id=provider_order_id,
                )
                self.db.add(subscription)
            else:
                subscription.plan = plan
                subscription.is_active = is_active
                if cakto_customer_id is not None:
                    subscription.cakto_customer_id = cakto_customer_id
                if cakto_transaction_id is not None:
                    subscription.cakto_transaction_id = cakto_transaction_id
                if expires_at is not None:
                    subscription.expires_at = expires_at
                if cakto_status is not None:
                    subscription.cakto_status = cakto_status
                if cakto_offer_name is not None:
                    subscription.cakto_offer_name = cakto_offer_name
                if cakto_due_date is not None:
                    subscription.cakto_due_date = cakto_due_date

                # Campos da Cakto (sempre sobrescreve)
                subscription.cakto_subscription_status = cakto_subscription_status
                subscription.cakto_payment_status = cakto_payment_status
                subscription.cakto_payment_method = cakto_payment_method

                # Generic provider fields
                if provider is not None:
                    subscription.provider = provider
                if provider_customer_id is not None:
                    subscription.provider_customer_id = provider_customer_id
                if provider_transaction_id is not None:
                    subscription.provider_transaction_id = provider_transaction_id
                if provider_status is not None:
                    subscription.provider_status = provider_status
                if provider_offer_name is not None:
                    subscription.provider_offer_name = provider_offer_name
                if provider_due_date is not None:
                    subscription.provider_due_date = provider_due_date
                subscription.provider_subscription_status = provider_subscription_status
                subscription.provider_payment_status = provider_payment_status
                subscription.provider_payment_method = provider_payment_method
                if provider_order_id is not None:
                    subscription.provider_order_id = provider_order_id

            self.db.commit()
            self.db.refresh(subscription)
            return subscription
        except Exception as e:
            self.db.rollback()
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Erro no upsert de subscription: {str(e)}")
            raise e
