from app.repositories.subscription_repository import SubscriptionRepository


class SubscriptionService:
    def __init__(self, repo: SubscriptionRepository):
        self.repo = repo

    def set_active(self, user_id: int, plan: str, is_active: bool):
        return self.repo.upsert(user_id=user_id, plan=plan, is_active=is_active)
