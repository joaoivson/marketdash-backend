import hashlib
import logging
from collections import Counter
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
          channelType
          attributionType
          globalCategoryLv1Name
          globalCategoryLv2Name
          globalCategoryLv3Name
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

        # Shopee Brasil opera em BRT (UTC-3). Os timestamps de purchaseTime
        # na API são relativos a BRT, então o range deve ser em BRT.
        BRT = timezone(timedelta(hours=-3))
        now = datetime.now(BRT)
        # Shopee Open API limita conversionReport aos "últimos 3 meses". Em meses
        # não-bissextos a janela vale 89 dias; usamos 88 para ter 1 dia de margem
        # contra drift de timezone entre nosso clock e o da API (erro 11001).
        start = now - timedelta(days=88)

        # A API restringe a janela de purchaseTime por request a poucos dias.
        # Para cobrir os 88 dias, iteramos em chunks e dentro de cada chunk
        # seguimos a paginação por scrollId.
        CHUNK_DAYS = 7

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
        # Contador sequencial para gerar row_hash único por item.
        # A API retorna múltiplos nós por conversionId (um por pedido/item),
        # e o mesmo (order_id, item_id) pode aparecer múltiplas vezes quando
        # o comprador adquiriu várias unidades/variantes do mesmo produto.
        # Não fazemos dedup — cada nó da API é processado como item legítimo.
        item_seq: Counter = Counter()

        total_processed = 0
        all_synced_order_ids: set[str] = set()

        chunk_start = start
        while chunk_start < now:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), now)
            ts_start = int(chunk_start.timestamp())
            ts_end = int(chunk_end.timestamp())

            logger.info(
                "Shopee sync user_id=%s: chunk %s → %s",
                user_id, chunk_start.date(), chunk_end.date(),
            )

            scroll_id: Optional[str] = None
            page_num = 0
            chunk_processed = 0

            while True:
                page_num += 1
                scroll_param = f', scrollId: "{scroll_id}"' if scroll_id else ""
                query = CONVERSIONS_QUERY % (ts_start, ts_end, scroll_param)

                try:
                    result = await shopee_graphql_client.execute_graphql(
                        integration.app_id, password, query
                    )
                except HTTPException as exc:
                    logger.error(
                        "Shopee paginação falhou user_id=%s chunk=%s→%s page=%d: %s",
                        user_id, chunk_start.date(), chunk_end.date(),
                        page_num, exc.detail,
                    )
                    raise

                report = (result.get("data") or {}).get("conversionReport") or {}
                nodes = report.get("nodes") or []
                page_info = report.get("pageInfo") or {}

                if not nodes:
                    break

                # DEBUG temporário: log sample da resposta pra investigar channelType vazio
                if page_num == 1:
                    sample_node = nodes[0]
                    sample_orders = sample_node.get("orders") or []
                    sample_items = (sample_orders[0].get("items") or []) if sample_orders else []
                    sample_item = sample_items[0] if sample_items else {}
                    logger.info(
                        "Shopee SAMPLE user=%s chunk=%s node_keys=%s item_keys=%s channelType=%r attributionType=%r utmContent=%r",
                        user_id, chunk_start.date(),
                        list(sample_node.keys()),
                        list(sample_item.keys()),
                        sample_item.get("channelType"),
                        sample_item.get("attributionType"),
                        sample_node.get("utmContent"),
                    )

                rows = []
                for node in nodes:
                    purchase_ts = node.get("purchaseTime")
                    try:
                        row_date = datetime.fromtimestamp(purchase_ts, tz=BRT).date()
                    except Exception:
                        row_date = now.date()

                    utm_content = str(node.get("utmContent") or "")
                    conversion_status = str(node.get("conversionStatus") or "")
                    # estimatedTotalCommission é a comissão líquida real do nó,
                    # equivalente a "Comissão líquida do afiliado(R$)" no CSV Shopee.
                    # itemCommission no nível do item não contém o valor completo
                    # (~10% menor). A soma de estimatedTotalCommission de todos os
                    # nós bate exatamente com o dashboard Shopee.
                    est_total_comm = float(node.get("estimatedTotalCommission") or 0)

                    # Coletar itens deste nó para distribuir comissão proporcionalmente
                    node_items = []
                    for order in (node.get("orders") or []):
                        order_id = str(order.get("orderId") or "")
                        order_status = str(order.get("orderStatus") or conversion_status)
                        for item in (order.get("items") or []):
                            item_id = str(item.get("itemId") or "")
                            actual_f = float(item.get("actualAmount") or 0)
                            comm_f = float(item.get("itemCommission") or 0)
                            qty = int(item.get("qty") or 1)
                            raw_channel = item.get("channelType")
                            raw_attribution = item.get("attributionType")
                            logger.debug(
                                "Shopee item order=%s item=%s channelType=%r attributionType=%r",
                                order_id, item_id, raw_channel, raw_attribution,
                            )
                            # Canal: usa channelType; fallback para attributionType se channelType vazio
                            channel_val = str(raw_channel or raw_attribution or "").strip()
                            node_items.append({
                                "order_id": order_id,
                                "order_status": order_status,
                                "item_id": item_id,
                                "item_name": str(item.get("itemName") or ""),
                                "actual_f": actual_f,
                                "item_comm_f": comm_f,
                                "qty": qty,
                                "channel_type": channel_val,
                                "category_lv1": str(item.get("globalCategoryLv1Name") or ""),
                                "category_lv2": str(item.get("globalCategoryLv2Name") or ""),
                                "category_lv3": str(item.get("globalCategoryLv3Name") or ""),
                            })

                    sum_item_comm = sum(ni["item_comm_f"] for ni in node_items)

                    for ni in node_items:
                        # Seq incremental para row_hash único
                        base_key = (ni["order_id"], ni["item_id"])
                        item_seq[base_key] += 1
                        seq = item_seq[base_key]

                        rh = _row_hash(user_id, ni["order_id"], ni["item_id"], str(row_date), str(seq))

                        # Revenue: actualAmount = "Valor de Compra(R$)" no CSV
                        # (0 para cancelados, com descontos aplicados)
                        revenue = ni["actual_f"]
                        if ni["order_status"].upper() in ["CANCELLED", "INVALID", "REJECTED"]:
                            revenue = 0.0

                        # Commission: distribui estimatedTotalCommission proporcionalmente
                        # ao itemCommission de cada item dentro do nó
                        if sum_item_comm > 0:
                            commission = ni["item_comm_f"] / sum_item_comm * est_total_comm
                        elif len(node_items) == 1:
                            commission = est_total_comm
                        else:
                            commission = 0.0

                        # Categoria: usar a mais específica disponível (lv3 > lv2 > lv1)
                        category = (
                            ni.get("category_lv3")
                            or ni.get("category_lv2")
                            or ni.get("category_lv1")
                            or ""
                        )

                        rows.append(
                            DatasetRow(
                                dataset_id=dataset.id,
                                user_id=user_id,
                                date=row_date,
                                platform="shopee",
                                product=ni["item_name"],
                                status=ni["order_status"],
                                category=category,
                                channel=ni.get("channel_type") or None,
                                sub_id1=utm_content,
                                order_id=ni["order_id"],
                                product_id=ni["item_id"],
                                revenue=revenue,
                                commission=commission,
                                quantity=ni["qty"],
                                row_hash=rh,
                            )
                        )

                if rows:
                    row_repo.bulk_create(rows)
                    total_processed += len(rows)
                    chunk_processed += len(rows)
                    # Acumula order_ids inseridos para dedup posterior
                    all_synced_order_ids.update(
                        r.order_id for r in rows if r.order_id
                    )

                if not page_info.get("hasNextPage"):
                    break

                scroll_id = page_info.get("scrollId")
                if not scroll_id:
                    break

            logger.info(
                "Shopee sync user_id=%s: chunk %s → %s concluído (%d rows, %d páginas)",
                user_id, chunk_start.date(), chunk_end.date(),
                chunk_processed, page_num,
            )

            # Avança para o próximo chunk (+1s para evitar overlap nas bordas
            # — mantém o seq counter consistente com chunks subsequentes).
            chunk_start = chunk_end + timedelta(seconds=1)

        # ── Dedup: remover rows de outros datasets (ex: CSV importado) ──
        # que possuam os mesmos order_ids já trazidos pela API Shopee.
        # Isso evita contagem dupla no dashboard quando o usuário importou
        # um CSV com dados Shopee e depois ativou a integração via API.
        if all_synced_order_ids:
            stale = (
                db.query(DatasetRow)
                .filter(
                    DatasetRow.user_id == user_id,
                    DatasetRow.dataset_id != dataset.id,
                    DatasetRow.order_id.in_(list(all_synced_order_ids)),
                )
                .delete(synchronize_session="fetch")
            )
            if stale:
                logger.info(
                    "Shopee dedup: removidos %d rows duplicados de outros datasets user_id=%s",
                    stale, user_id,
                )

        logger.info(
            "Shopee sync user_id=%s: %d rows processados",
            user_id, total_processed,
        )
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
