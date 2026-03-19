import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.shopee_integration import ShopeeIntegration

logger = logging.getLogger(__name__)


class ShopeeIntegrationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user_id(self, user_id: int) -> Optional[ShopeeIntegration]:
        return (
            self.db.query(ShopeeIntegration)
            .filter(ShopeeIntegration.user_id == user_id)
            .first()
        )

    def get_all_active(self) -> List[ShopeeIntegration]:
        """Retorna todas as integrações ativas — usado pelo job diário."""
        return (
            self.db.query(ShopeeIntegration)
            .filter(ShopeeIntegration.is_active == True)
            .all()
        )

    def upsert(self, user_id: int, app_id: str, encrypted_password: str) -> ShopeeIntegration:
        existing = self.get_by_user_id(user_id)
        if existing:
            existing.app_id = app_id
            existing.encrypted_password = encrypted_password
            existing.is_active = True
            self.db.flush()
            return existing

        integration = ShopeeIntegration(
            user_id=user_id,
            app_id=app_id,
            encrypted_password=encrypted_password,
            is_active=True,
        )
        self.db.add(integration)
        self.db.flush()
        return integration

    def update_last_sync(self, user_id: int) -> None:
        from sqlalchemy.sql import func
        self.db.query(ShopeeIntegration).filter(
            ShopeeIntegration.user_id == user_id
        ).update({"last_sync_at": func.now()})
        self.db.flush()

    def delete_by_user_id(self, user_id: int) -> bool:
        deleted = (
            self.db.query(ShopeeIntegration)
            .filter(ShopeeIntegration.user_id == user_id)
            .delete()
        )
        self.db.flush()
        return deleted > 0
