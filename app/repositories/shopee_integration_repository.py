import logging
from datetime import datetime, timezone
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
        """Retorna integrações ativas e NÃO pausadas, mais atrasadas primeiro (NULLS FIRST).

        Ordenar por last_sync_at evita starvation: contas grandes/no fim da
        lista indefinida do Postgres ficavam dias sem sync quando o cron
        horário overlapping ou o processo da API reiniciava no meio do lote.

        Contas com sync_paused_at preenchido (credencial inválida / never-synced
        crônico) ficam de fora até o usuário reconectar.
        """
        return (
            self.db.query(ShopeeIntegration)
            .filter(
                ShopeeIntegration.is_active == True,  # noqa: E712
                ShopeeIntegration.sync_paused_at.is_(None),
            )
            .order_by(ShopeeIntegration.last_sync_at.asc().nullsfirst())
            .all()
        )

    def upsert(self, user_id: int, app_id: str, encrypted_password: str) -> ShopeeIntegration:
        existing = self.get_by_user_id(user_id)
        if existing:
            existing.app_id = app_id
            existing.encrypted_password = encrypted_password
            existing.is_active = True
            # Reconectar = nova chance: limpa pausa do cron.
            existing.sync_paused_at = None
            existing.sync_pause_reason = None
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

    def pause_sync(self, user_id: int, reason: str) -> bool:
        """Marca integração para o cron não reenfileirar. Idempotente."""
        integ = self.get_by_user_id(user_id)
        if not integ:
            return False
        if integ.sync_paused_at is not None:
            return False
        integ.sync_paused_at = datetime.now(timezone.utc)
        integ.sync_pause_reason = reason[:500]
        self.db.flush()
        logger.warning(
            "Shopee sync pausado user_id=%s reason=%s — cron vai pular até reconectar",
            user_id,
            reason,
        )
        return True

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
