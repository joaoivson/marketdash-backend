import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy.orm import Session
from sqlalchemy.sql import func

from app.models.facebook_integration import FacebookIntegration

logger = logging.getLogger(__name__)


class FacebookIntegrationRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user_id(self, user_id: int) -> Optional[FacebookIntegration]:
        return (
            self.db.query(FacebookIntegration)
            .filter(FacebookIntegration.user_id == user_id)
            .first()
        )

    def get_all_active(self) -> List[FacebookIntegration]:
        """Integrações ativas com conta selecionada — usado pelo job diário."""
        return (
            self.db.query(FacebookIntegration)
            .filter(
                FacebookIntegration.is_active == True,  # noqa: E712
                FacebookIntegration.ad_account_id.isnot(None),
            )
            .all()
        )

    def upsert_token(
        self,
        user_id: int,
        encrypted_access_token: str,
        fb_user_id: Optional[str],
        fb_user_name: Optional[str],
        scopes: Optional[str],
        token_expires_at: Optional[datetime],
    ) -> FacebookIntegration:
        existing = self.get_by_user_id(user_id)
        if existing:
            existing.encrypted_access_token = encrypted_access_token
            existing.fb_user_id = fb_user_id
            existing.fb_user_name = fb_user_name
            existing.scopes = scopes
            existing.token_expires_at = token_expires_at
            existing.is_active = True
            self.db.flush()
            return existing

        integration = FacebookIntegration(
            user_id=user_id,
            encrypted_access_token=encrypted_access_token,
            fb_user_id=fb_user_id,
            fb_user_name=fb_user_name,
            scopes=scopes,
            token_expires_at=token_expires_at,
            is_active=True,
        )
        self.db.add(integration)
        self.db.flush()
        return integration

    def set_ad_account(self, user_id: int, ad_account_id: str, ad_account_name: Optional[str]) -> Optional[FacebookIntegration]:
        integration = self.get_by_user_id(user_id)
        if not integration:
            return None
        integration.ad_account_id = ad_account_id
        integration.ad_account_name = ad_account_name
        self.db.flush()
        return integration

    def update_last_sync(self, user_id: int) -> None:
        self.db.query(FacebookIntegration).filter(
            FacebookIntegration.user_id == user_id
        ).update({"last_sync_at": func.now()})
        self.db.flush()

    def delete_by_user_id(self, user_id: int) -> bool:
        deleted = (
            self.db.query(FacebookIntegration)
            .filter(FacebookIntegration.user_id == user_id)
            .delete()
        )
        self.db.flush()
        return deleted > 0
