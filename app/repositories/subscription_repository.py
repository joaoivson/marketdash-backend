from typing import Optional

from sqlalchemy.orm import Session

from app.models.subscription import Subscription


class SubscriptionRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user_id(self, user_id: int) -> Optional[Subscription]:
        return self.db.query(Subscription).filter(Subscription.user_id == user_id).first()

    def upsert(self, user_id: int, plan: str, is_active: bool) -> Subscription:
        subscription = self.get_by_user_id(user_id)
        if not subscription:
            subscription = Subscription(user_id=user_id, plan=plan, is_active=is_active)
            self.db.add(subscription)
        else:
            subscription.plan = plan
            subscription.is_active = is_active
        self.db.commit()
        self.db.refresh(subscription)
        return subscription
