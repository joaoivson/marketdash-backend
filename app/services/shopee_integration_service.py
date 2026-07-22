import hashlib
import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlparse

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_value, encrypt_value
from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.schemas.shopee_integration import ShopeeIntegrationResponse
from app.services import shopee_graphql_client
from app.utils.shopee_normalize import normalize_order_status, normalize_attribution_type

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


# Mutation generateShortLink — valores inlinados na string da query (NÃO pré-serializar
# o payload; execute_graphql serializa o payload UMA vez e assina sobre ele). Chaves do
# objeto GraphQL ficam SEM aspas; valores escapados via json.dumps. %s na ordem:
#   1) originUrl (string JSON-escaped)  2) subIds (array GraphQL "[...]")
GENERATE_SHORT_LINK_TEMPLATE = """
mutation {
  generateShortLink(input: {originUrl: %s, subIds: %s}) {
    shortLink
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

    async def generate_short_link(
        self, user_id: int, origin_url: str, sub_id: Optional[str]
    ) -> str:
        """
        Gera um short link de afiliado Shopee a partir de uma URL de origem.
        Efêmero: não persiste nada nem toca custom_links.
        """
        # Valida que a URL é da Shopee ANTES de chamar a API — domínio inválido
        # faz a Shopee retornar erro 11001 (URL fora do programa de afiliados).
        parsed = urlparse(origin_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https") or not host:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL precisa ser da Shopee",
            )
        is_shopee = host == "shopee.com.br" or host.startswith("shopee.") or ".shopee." in host
        if not is_shopee:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="URL precisa ser da Shopee",
            )

        subs = [sub_id] if sub_id else []
        sub_ids_gql = "[" + ",".join(json.dumps(s) for s in subs) + "]"
        mutation = GENERATE_SHORT_LINK_TEMPLATE % (json.dumps(origin_url), sub_ids_gql)

        # Erros da Shopee (HTTPException levantadas por execute_graphql) propagam.
        result = await self.proxy_graphql(user_id, mutation, None)
        short_link = (
            (result.get("data") or {})
            .get("generateShortLink", {})
            .get("shortLink")
        )
        if not short_link:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não foi possível gerar o short link.",
            )

        logger.info("Shopee short link gerado user_id=%s: %s", user_id, short_link)
        return short_link

    # ------------------------------------------------------------------ #
    #  Sincronização de dados                                              #
    # ------------------------------------------------------------------ #

    async def sync_commissions(self, user_id: int, db: Session, days_back: int = 88) -> int:
        """Sincroniza comissões Shopee para os últimos N dias (padrão: 88 dias = ~3 meses).

        Args:
            user_id: ID do usuário
            db: Sessão do banco de dados
            days_back: Número de dias a sincronizar (7, 14, 30, 60, 88, 90, etc.)
                      88 = sync incremental de 3 meses (padrão)
                      90 = reconcile completo (~3 meses + 2 dias de margem)

        Returns:
            Número de linhas processadas da API
        """
        integration = self.repo.get_by_user_id(user_id)
        if not integration or not integration.is_active:
            return 0

        # Captura credenciais ANTES do commit do dataset (expire_on_commit=True expira o objeto
        # `integration`; usar `integration.app_id` no loop recarregaria do banco, reabrindo
        # transação durante as chamadas lentas da API).
        password = decrypt_value(integration.encrypted_password)
        app_id = integration.app_id

        # Shopee Brasil opera em BRT (UTC-3). Os timestamps de purchaseTime
        # na API são relativos a BRT, então o range deve ser em BRT.
        BRT = timezone(timedelta(hours=-3))
        now = datetime.now(BRT)
        # Clampear days_back ao limite de 90 dias (API não retorna além disso)
        days_back = min(max(days_back, 1), 90)
        start = now - timedelta(days=days_back)

        # A API restringe a janela de purchaseTime por request a poucos dias.
        # Para cobrir os 88 dias, iteramos em chunks e dentro de cada chunk
        # seguimos a paginação por scrollId.
        CHUNK_DAYS = 7

        dataset = _get_or_create_shopee_dataset(user_id, "transaction", db)
        ds_id = dataset.id  # captura o id ANTES do commit (expire_on_commit expira o objeto)
        # Commita o dataset AGORA (transação curta) pra NÃO segurar uma transação aberta durante
        # as chamadas lentas da API. O DELETE+REINSERT da janela é feito de forma ATÔMICA só no
        # FIM (depois de buscar tudo) — ver o bloco "Re-sync ATÔMICO" no fim de sync_commissions.
        db.commit()

        row_repo = DatasetRowRepository(db)
        # Contador sequencial para gerar row_hash único por item.
        # A API retorna múltiplos nós por conversionId (um por pedido/item),
        # e o mesmo (order_id, item_id) pode aparecer múltiplas vezes quando
        # o comprador adquiriu várias unidades/variantes do mesmo produto.
        # Não fazemos dedup — cada nó da API é processado como item legítimo.
        item_seq: Counter = Counter()

        total_processed = 0
        all_synced_order_ids: set[str] = set()
        # Acumula TODAS as rows de TODAS as páginas/chunks em memória (a parte lenta = API).
        # O INSERT no banco só acontece no fim, de forma atômica.
        all_rows: list[DatasetRow] = []

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
                        app_id, password, query
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

                page_rows = []
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
                            # Canal: usa SOMENTE channelType (o campo de origem real da Shopee).
                            # NÃO faz fallback p/ attributionType (que é direto/cookie, gravado à parte
                            # em attribution_type) — senão o donut "Comissão por canal" do dashboard
                            # mostrava "ORDERED_IN_SAME_SHOP"/genérico em vez do canal. Vazio → o front
                            # agrupa como "Outros".
                            channel_val = str(raw_channel or "").strip()
                            node_items.append({
                                "order_id": order_id,
                                "order_status": order_status,
                                "item_id": item_id,
                                "item_name": str(item.get("itemName") or ""),
                                "actual_f": actual_f,
                                "item_comm_f": comm_f,
                                "qty": qty,
                                "channel_type": channel_val,
                                # A API retorna texto ("Ordered in Same Shop"/"...Different Shop");
                                # normaliza p/ a constante canônica usada no KPI "Diretos"
                                # (ORDERED_IN_SAME_SHOP). Sem isso o texto cru nunca casava → 0 diretos.
                                "attribution_type": normalize_attribution_type(raw_attribution),
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

                        # Categoria PRINCIPAL (nível 1) — o dashboard agrupa pela categoria-mãe
                        # (Casa e Decoração, Roupas Femininas, Beleza...). Fallback p/ os níveis
                        # abaixo só se a lv1 não vier.
                        category = (
                            ni.get("category_lv1")
                            or ni.get("category_lv2")
                            or ni.get("category_lv3")
                            or ""
                        )

                        page_rows.append(
                            DatasetRow(
                                dataset_id=ds_id,
                                user_id=user_id,
                                date=row_date,
                                platform="shopee",
                                product=ni["item_name"],
                                # Status em PT canônico (Concluído/Pendente/Cancelado), igual ao CSV.
                                # A checagem de revenue acima usa o status raw (EN) da API, então
                                # normalizar aqui não afeta o zeramento de cancelados/inválidos.
                                status=normalize_order_status(ni["order_status"]),
                                category=category,
                                channel=ni.get("channel_type") or None,
                                attribution_type=ni.get("attribution_type") or None,
                                sub_id1=utm_content,
                                order_id=ni["order_id"],
                                product_id=ni["item_id"],
                                revenue=revenue,
                                commission=commission,
                                quantity=ni["qty"],
                                row_hash=rh,
                            )
                        )

                if page_rows:
                    all_rows.extend(page_rows)
                    total_processed += len(page_rows)
                    chunk_processed += len(page_rows)
                    # Acumula order_ids para o dedup posterior
                    all_synced_order_ids.update(
                        r.order_id for r in page_rows if r.order_id
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

        # ── Re-sync ATÔMICO ──────────────────────────────────────────────────────
        # Toda a API já foi buscada (parte lenta, SEM transação aberta). Agora a troca dos dados
        # acontece numa ÚNICA transação curta: DELETE da janela de 88 dias + dedup + reinsert em
        # lotes, com COMMIT único (no sync_user). Leitores (dashboard) enxergam os dados ANTIGOS
        # até o commit; depois, os novos — NUNCA o estado vazio (antes piscava ~5 min por sync).
        # Se algo falhar antes do commit → rollback e os dados ANTIGOS ficam intactos.
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
                "Shopee full refresh: removidos %d rows antigos user_id=%s", deleted, user_id,
            )

        # ── Dedup: remover rows de outros datasets (ex: CSV importado) ──
        # que possuam os mesmos order_ids já trazidos pela API Shopee.
        # Isso evita contagem dupla no dashboard quando o usuário importou
        # um CSV com dados Shopee e depois ativou a integração via API.
        if all_synced_order_ids:
            stale = (
                db.query(DatasetRow)
                .filter(
                    DatasetRow.user_id == user_id,
                    DatasetRow.dataset_id != ds_id,
                    DatasetRow.order_id.in_(list(all_synced_order_ids)),
                )
                .delete(synchronize_session="fetch")
            )
            if stale:
                logger.info(
                    "Shopee dedup: removidos %d rows duplicados de outros datasets user_id=%s",
                    stale, user_id,
                )

        # Reinsert em LOTES (respeita o limite de ~65k binds por statement do Postgres), SEM
        # commit — o sync_user commita DELETE + dedup + todos os inserts numa transação só.
        INSERT_BATCH = 500
        for i in range(0, len(all_rows), INSERT_BATCH):
            row_repo.bulk_create(all_rows[i:i + INSERT_BATCH], commit=False)

        logger.info(
            "Shopee sync user_id=%s: %d rows processados",
            user_id, total_processed,
        )
        return total_processed

    async def sync_user(self, user_id: int, db: Session, days_back: int = 88) -> int:
        """Sincroniza comissões e atualiza last_sync_at. Retorna número de conversões inseridas.

        Args:
            user_id: ID do usuário
            db: Sessão do banco de dados
            days_back: Número de dias a sincronizar (padrão 88)

        Serializa por usuário com advisory lock do Postgres numa conexão DEDICADA (autocommit):
        se JÁ há um sync em andamento para este user — outro worker, concurrency>1, ou tarefa
        duplicada na fila — esta NO-OP na hora (retorna 0) em vez de rodar um DELETE+REINSERT
        concorrente. Era a causa raiz da corrida/churn (dois syncs apagando+reinserindo as mesmas
        linhas, lock contention, dashboard piscando zero) e do acúmulo de fila (duplicados que
        levavam ~5 min cada agora viram no-op instantâneo). A conexão dedicada mantém o lock vivo
        através dos commits por chunk; fechá-la no finally libera o lock mesmo em falha. Fail-open:
        se o lock não puder ser adquirido por erro de infra, segue sem serialização (comportamento
        antigo) em vez de travar o sync.
        """
        LOCK_NS = 819100  # namespace fixo p/ não colidir com outros advisory locks da base
        lock_conn = None
        try:
            lock_conn = db.get_bind().connect().execution_options(isolation_level="AUTOCOMMIT")
            acquired = lock_conn.execute(
                text("SELECT pg_try_advisory_lock(:ns, :uid)"),
                {"ns": LOCK_NS, "uid": user_id},
            ).scalar()
            if not acquired:
                lock_conn.close()
                logger.info(
                    "Shopee sync user_id=%s: já há um sync em andamento — ignorando (no-op)",
                    user_id,
                )
                return 0
        except Exception as exc:
            logger.warning(
                "Shopee sync user_id=%s: advisory lock indisponível (%s); seguindo sem lock",
                user_id, exc,
            )
            if lock_conn is not None:
                try:
                    lock_conn.close()
                except Exception:
                    pass
                lock_conn = None

        try:
            commissions = await self.sync_commissions(user_id, db, days_back=days_back)
            self.repo.update_last_sync(user_id)
            db.commit()
            logger.info(
                "Shopee sync concluído user_id=%s: %d conversões (%d dias)",
                user_id, commissions, days_back,
            )
            return commissions
        except Exception as exc:
            db.rollback()
            logger.error("Shopee sync falhou user_id=%s: %s", user_id, exc)
            raise
        finally:
            if lock_conn is not None:
                try:
                    lock_conn.execute(
                        text("SELECT pg_advisory_unlock(:ns, :uid)"),
                        {"ns": LOCK_NS, "uid": user_id},
                    )
                except Exception:
                    pass
                try:
                    lock_conn.close()  # fechar a conexão também libera advisory locks remanescentes
                except Exception:
                    pass


async def run_shopee_sync_all(days_back: int = 7) -> dict:
    """Sincroniza TODOS os usuários Shopee ativos INLINE — sem Celery/worker.

    Disparado pelo cron do Supabase (pg_cron → pg_net → POST /internal/cron/shopee-sync),
    que agenda esta função num BackgroundTask do FastAPI: roda no próprio processo da API.
    Contas is_demo são puladas. Falha de um usuário não derruba os demais.
    """
    from app.db.session import SessionLocal
    from app.models.user import User

    db0 = SessionLocal()
    try:
        integrations = ShopeeIntegrationRepository(db0).get_all_active()
        user_ids: list[int] = []
        for integ in integrations:
            user = db0.query(User).filter(User.id == integ.user_id).first()
            if user and getattr(user, "is_demo", False):
                continue
            user_ids.append(integ.user_id)
    finally:
        db0.close()

    synced = 0
    for uid in user_ids:
        db = SessionLocal()
        try:
            svc = ShopeeIntegrationService(ShopeeIntegrationRepository(db))
            await svc.sync_user(uid, db, days_back=days_back)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Shopee sync inline falhou user_id=%s: %s", uid, exc)
            db.rollback()
        finally:
            db.close()
    logger.info(
        "Shopee sync inline (pg_cron, sem worker): %d/%d usuários days_back=%d",
        synced, len(user_ids), days_back,
    )
    return {"synced": synced, "total": len(user_ids), "days_back": days_back}
