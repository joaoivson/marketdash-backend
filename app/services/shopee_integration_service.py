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
        start = now - timedelta(days=90)
        ts_start = int(start.timestamp())
        ts_end = int(now.timestamp())

        dataset = _get_or_create_shopee_dataset(user_id, "transaction", db)

        # Full refresh: apaga dados Shopee do período e re-insere com valores
        # corretos da API, garantindo que revenue/commission estejam sempre atualizados.
        deleted = (
            db.query(DatasetRow)
            .filter(
                DatasetRow.user_id == user_id,
                DatasetRow.platform == "shopee",
                DatasetRow.date >= start.date(),
            )
            .delete(synchronize_session="fetch")
        )
        if deleted:
            logger.info(
                "Shopee full refresh: removidos %d rows antigos user_id=%s",
                deleted, user_id,
            )

        row_repo = DatasetRowRepository(db)
        seen_keys: set[tuple[str, str]] = set()

        total_processed = 0
        scroll_id: Optional[str] = None

        # Diagnóstico: somas por campo para validar mapeamento
        from collections import defaultdict
        debug_monthly: dict[str, dict[str, float]] = defaultdict(
            lambda: {
                "itemPrice": 0.0, "actualAmount": 0.0,
                "itemPrice_x_qty": 0.0, "actualAmount_x_qty": 0.0,
                "itemCommission": 0.0, "estimatedTotalCommission": 0.0,
                "netCommission": 0.0, "count": 0,
            }
        )
        debug_statuses: set[str] = set()

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
                est_total_comm = float(node.get("estimatedTotalCommission") or 0)
                net_comm = float(node.get("netCommission") or 0)

                for order in (node.get("orders") or []):
                    order_id = str(order.get("orderId") or "")
                    order_status = str(order.get("orderStatus") or conversion_status)

                    for item_idx, item in enumerate(order.get("items") or []):
                        item_id = str(item.get("itemId") or "")
                        actual_amount = item.get("actualAmount")
                        actual_f = float(actual_amount) if actual_amount is not None else 0.0
                        # Chave inclui actualAmount para distinguir variantes
                        # do mesmo item (model IDs diferentes) no mesmo pedido
                        order_item_key = (order_id, item_id, f"{actual_f:.4f}")

                        # Dedup dentro da mesma página/sync
                        if order_item_key in seen_keys:
                            continue
                        seen_keys.add(order_item_key)

                        rh = _row_hash(user_id, order_id, item_id, str(row_date), f"{actual_f:.4f}")
                        qty = int(item.get("qty") or 1)

                        item_price = item.get("itemPrice")
                        item_commission = item.get("itemCommission")

                        price_f = float(item_price) if item_price is not None else 0.0
                        comm_f = float(item_commission) if item_commission is not None else 0.0

                        # Diagnóstico por mês
                        month_key = row_date.strftime("%Y-%m")
                        m = debug_monthly[month_key]
                        m["itemPrice"] += price_f
                        m["actualAmount"] += actual_f
                        m["itemPrice_x_qty"] += price_f * qty
                        m["actualAmount_x_qty"] += actual_f * qty
                        m["itemCommission"] += comm_f
                        m["estimatedTotalCommission"] += est_total_comm
                        m["netCommission"] += net_comm
                        m["count"] += 1
                        debug_statuses.add(order_status)

                        # Revenue: usa actualAmount (= "Valor de Compra" no CSV Shopee)
                        # itemPrice é o preço de vitrine e NÃO zera para pedidos cancelados,
                        # inflando o faturamento. actualAmount reflete o valor real da compra.
                        revenue = actual_f

                        # Commission: usa itemCommission
                        commission = comm_f

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
                                revenue=revenue,
                                commission=commission,
                                quantity=qty,
                                row_hash=rh,
                            )
                        )

            if rows:
                row_repo.bulk_create(rows)
                total_processed += len(rows)

            if not page_info.get("hasNextPage"):
                break

            scroll_id = page_info.get("scrollId")
            if not scroll_id:
                break

        # Log diagnóstico para validar mapeamento de campos
        logger.info("=== SHOPEE DIAGNÓSTICO user_id=%s ===", user_id)
        logger.info("Statuses encontrados: %s", debug_statuses)
        for month, vals in sorted(debug_monthly.items()):
            logger.info(
                "Mês %s | items=%d | itemPrice=%.2f | actualAmount=%.2f | "
                "itemPrice*qty=%.2f | actualAmount*qty=%.2f | "
                "itemCommission=%.2f | estTotalComm=%.2f | netComm=%.2f",
                month, int(vals["count"]),
                vals["itemPrice"], vals["actualAmount"],
                vals["itemPrice_x_qty"], vals["actualAmount_x_qty"],
                vals["itemCommission"],
                vals["estimatedTotalCommission"], vals["netCommission"],
            )
        logger.info("=== FIM DIAGNÓSTICO ===")

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
