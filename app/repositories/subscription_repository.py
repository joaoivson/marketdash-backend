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
        expires_at = None
    ) -> Subscription:
        subscription = self.get_by_user_id(user_id)
        if not subscription:
            subscription = Subscription(
                user_id=user_id, 
                plan=plan, 
                is_active=is_active,
                cakto_customer_id=cakto_customer_id,
                cakto_transaction_id=cakto_transaction_id,
                expires_at=expires_at
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
        self.db.commit()
        self.db.refresh(subscription)
        return subscription
