import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_value, encrypt_value
from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.schemas.shopee_integration import ShopeeIntegrationResponse
from app.services import shopee_graphql_client

logger = logging.getLogger(__name__)

# Campos de conversionReport com paginação por scrollId
CONVERSIONS_QUERY = """
{
  conversionReport(purchaseTimeStart: %d, purchaseTimeEnd: %d, limit: 100%s) {
    pageInfo { hasNextPage scrollId }
    nodes {
      purchaseTime
      conversionId
      conversionStatus
      estimatedTotalCommission
      netCommission
      utmContent
      orders {
        orderId
        orderStatus
        items {
          itemId
          itemName
          itemPrice
          actualAmount
          qty
          itemCommission
          shopName
          fraudStatus
        }
      }
    }
  }
}
"""


def _row_hash(user_id: int, *parts: str) -> str:
    raw = ":".join([str(user_id)] + list(parts))
    return hashlib.md5(raw.encode()).hexdigest()


def _get_or_create_shopee_dataset(user_id: int, dataset_type: str, db: Session) -> Dataset:
    filename = f"shopee_api_sync_{dataset_type}"
    existing = (
        db.query(Dataset)
        .filter(Dataset.user_id == user_id, Dataset.filename == filename)
        .first()
    )
    if existing:
        return existing

    dataset = Dataset(
        user_id=user_id,
        filename=filename,
        type=dataset_type,
        status="completed",
        row_count=0,
    )
    db.add(dataset)
    db.flush()
    return dataset


class ShopeeIntegrationService:
    def __init__(self, repo: ShopeeIntegrationRepository):
        self.repo = repo

    # ------------------------------------------------------------------ #
    #  CRUD de credenciais                                                 #
    # ------------------------------------------------------------------ #

    def save_credentials(self, user_id: int, app_id: str, password: str) -> ShopeeIntegrationResponse:
        encrypted = encrypt_value(password)
        integration = self.repo.upsert(user_id, app_id, encrypted)
        self.repo.db.commit()
        return ShopeeIntegrationResponse.model_validate(integration)

    def get_status(self, user_id: int) -> Optional[ShopeeIntegrationResponse]:
        integration = self.repo.get_by_user_id(user_id)
        if not integration:
            return None
        return ShopeeIntegrationResponse.model_validate(integration)

    def delete_credentials(self, user_id: int) -> None:
        deleted = self.repo.delete_by_user_id(user_id)
        self.repo.db.commit()
        if not deleted:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Integração Shopee não encontrada.",
            )

    async def proxy_graphql(self, user_id: int, query: str, variables: Optional[dict] = None) -> dict:
        integration = self.repo.get_by_user_id(user_id)
        if not integration or not integration.is_active:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Integração Shopee não configurada ou inativa.",
            )
        password = decrypt_value(integration.encrypted_password)
        return await shopee_graphql_client.execute_graphql(
            integration.app_id, password, query, variables
        )

    # ------------------------------------------------------------------ #
    #  Sincronização de dados                                              #
    # ------------------------------------------------------------------ #

    async def sync_commissions(self, user_id: int, db: Session) -> int:
        integration = self.repo.get_by_user_id(user_id)
        if not integration or not integration.is_active:
            return 0

        password = decrypt_value(integration.encrypted_password)
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=60)
        ts_start = int(start.timestamp())
        ts_end = int(now.timestamp())

        dataset = _get_or_create_shopee_dataset(user_id, "transaction", db)
        row_repo = DatasetRowRepository(db)
        existing_hashes = row_repo.get_existing_hashes(user_id)
        existing_order_item_keys = row_repo.get_existing_order_item_keys(user_id)

        total_processed = 0
        scroll_id: Optional[str] = None

        while True:
            scroll_param = f', scrollId: "{scroll_id}"' if scroll_id else ""
            query = CONVERSIONS_QUERY % (ts_start, ts_end, scroll_param)

            try:
                result = await shopee_graphql_client.execute_graphql(
                    integration.app_id, password, query
                )
            except HTTPException:
                break

            report = (result.get("data") or {}).get("conversionReport") or {}
            nodes = report.get("nodes") or []
            page_info = report.get("pageInfo") or {}

            if not nodes:
                break

            rows = []
            for node in nodes:
                purchase_ts = node.get("purchaseTime")
                try:
                    row_date = datetime.fromtimestamp(purchase_ts, tz=timezone.utc).date()
                except Exception:
                    row_date = now.date()

                utm_content = str(node.get("utmContent") or "")
                conversion_status = str(node.get("conversionStatus") or "")

                for order in (node.get("orders") or []):
                    order_id = str(order.get("orderId") or "")
                    order_status = str(order.get("orderStatus") or conversion_status)

                    for item in (order.get("items") or []):
                        item_id = str(item.get("itemId") or "")
                        rh = _row_hash(user_id, order_id, item_id, str(row_date))
                        order_item_key = (order_id, item_id)

                        if rh in existing_hashes or order_item_key in existing_order_item_keys:
                            continue

                        rows.append(
                            DatasetRow(
                                dataset_id=dataset.id,
                                user_id=user_id,
                                date=row_date,
                                platform="shopee",
                                product=str(item.get("itemName") or ""),
                                status=order_status,
                                sub_id1=utm_content,
                                order_id=order_id,
                                product_id=item_id,
                                revenue=float(item.get("actualAmount") or item.get("itemPrice") or 0),
                                commission=float(item.get("itemCommission") or 0),
                                quantity=int(item.get("qty") or 1),
                                row_hash=rh,
                            )
                        )
                        existing_hashes.add(rh)
                        existing_order_item_keys.add(order_item_key)

            if rows:
                row_repo.bulk_create(rows)
                total_processed += len(rows)

            if not page_info.get("hasNextPage"):
                break

            scroll_id = page_info.get("scrollId")
            if not scroll_id:
                break

        return total_processed

    async def sync_user(self, user_id: int, db: Session) -> int:
        """Sincroniza comissões e atualiza last_sync_at. Retorna número de conversões inseridas."""
        try:
            commissions = await self.sync_commissions(user_id, db)
            self.repo.update_last_sync(user_id)
            db.commit()
            logger.info(
                "Shopee sync concluído user_id=%s: %d conversões",
                user_id, commissions,
            )
            return commissions
        except Exception as exc:
            db.rollback()
            logger.error("Shopee sync falhou user_id=%s: %s", user_id, exc)
            raise
