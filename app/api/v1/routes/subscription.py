from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService

router = APIRouter(tags=["subscription"])


@router.get("/status")
def get_subscription_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retorna o status da assinatura do usuário atual."""
    subscription_service = SubscriptionService(SubscriptionRepository(db))
    status_data = subscription_service.get_subscription_status(current_user.id)
    return status_data
