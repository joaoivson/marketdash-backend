import logging
from datetime import date
from typing import Dict, List, Optional

from sqlalchemy import distinct, func
from sqlalchemy.orm import Session

from app.models.ad_spend import AdSpend
from app.models.campaign import Campaign, CampaignDailyInsight
from app.models.dataset_row import DatasetRow

logger = logging.getLogger(__name__)

# Atribuição DIRETA da Shopee: comprou na mesma loja do clique (ORDERED_IN_SAME_SHOP);
# o resto (ex.: ORDERED_IN_DIFFERENT_SHOP) é cookie/cross-shop. Vem do attributionType.
DIRECT_ATTRIBUTION_TYPE = "ORDERED_IN_SAME_SHOP"


def _norm_sub_id():
    """Normaliza o sub_id1 removendo os separadores vazios finais da Shopee.

    A Shopee grava o utmContent como 'subid1-subid2-...-subid5'; com só o subid1
    preenchido, sobra '----' no fim (ex.: 'basecoreana100280526----'). Removemos os
    '-' finais para casar com o sub_id "limpo" que o usuário vincula à campanha e
    para exibir valores legíveis no modal.
    """
    return func.rtrim(DatasetRow.sub_id1, "-")


class CampaignRepository:
    def __init__(self, db: Session):
        self.db = db

    # ----------------------------- campaigns ----------------------------- #

    def list_by_user(self, user_id: int) -> List[Campaign]:
        return (
            self.db.query(Campaign)
            .filter(Campaign.user_id == user_id)
            .order_by(Campaign.name.asc())
            .all()
        )

    def get_by_id(self, campaign_id: int, user_id: int) -> Optional[Campaign]:
        return (
            self.db.query(Campaign)
            .filter(Campaign.id == campaign_id, Campaign.user_id == user_id)
            .first()
        )

    def get_by_fb_id(self, user_id: int, fb_campaign_id: str) -> Optional[Campaign]:
        return (
            self.db.query(Campaign)
            .filter(Campaign.user_id == user_id, Campaign.fb_campaign_id == fb_campaign_id)
            .first()
        )

    def upsert_campaign(self, user_id: int, fb_campaign_id: str, fields: dict) -> Campaign:
        """Cria ou atualiza uma campanha pelo (user_id, fb_campaign_id).

        IMPORTANTE: preserva `sub_id` (vínculo manual) entre syncs — só os
        campos vindos do Facebook são sobrescritos.
        """
        existing = self.get_by_fb_id(user_id, fb_campaign_id)
        if existing:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.last_synced_at = func.now()
            self.db.flush()
            return existing

        campaign = Campaign(
            user_id=user_id,
            fb_campaign_id=fb_campaign_id,
            last_synced_at=func.now(),
            **fields,
        )
        self.db.add(campaign)
        self.db.flush()
        return campaign

    def set_sub_id(self, campaign: Campaign, sub_id: Optional[str]) -> Campaign:
        campaign.sub_id = sub_id
        self.db.flush()
        return campaign

    def find_by_sub_id(
        self, user_id: int, sub_id: str, exclude_campaign_id: Optional[int] = None
    ) -> Optional[Campaign]:
        """Campanha (do usuário) já vinculada a `sub_id`. Usado para garantir o vínculo 1:1."""
        query = self.db.query(Campaign).filter(
            Campaign.user_id == user_id, Campaign.sub_id == sub_id
        )
        if exclude_campaign_id is not None:
            query = query.filter(Campaign.id != exclude_campaign_id)
        return query.first()

    def linked_sub_ids(self, user_id: int) -> Dict[str, tuple]:
        """{ sub_id: (campaign_id, campaign_name) } de todas as campanhas já vinculadas."""
        rows = (
            self.db.query(Campaign.id, Campaign.name, Campaign.sub_id)
            .filter(Campaign.user_id == user_id, Campaign.sub_id.isnot(None))
            .all()
        )
        return {r.sub_id: (r.id, r.name) for r in rows}

    # -------------------------- daily insights --------------------------- #

    def earliest_insight_date(self, user_id: int):
        """Data do insight mais antigo do usuário — usado pra decidir se precisa backfill de 90d."""
        return (
            self.db.query(func.min(CampaignDailyInsight.date))
            .filter(CampaignDailyInsight.user_id == user_id)
            .scalar()
        )

    def upsert_insights(self, items: List[CampaignDailyInsight]) -> int:
        """Upsert de insights por (campaign_id, date) — preserva histórico (sem delete).

        Idempotente: re-sincronizar a janela sobrescreve spend/clicks/etc do dia, sem
        apagar dias fora da janela. O banco é a fonte de verdade, não a API.
        """
        if not items:
            return 0
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        payload = [
            {
                "user_id": it.user_id,
                "campaign_id": it.campaign_id,
                "fb_campaign_id": it.fb_campaign_id,
                "date": it.date,
                "spend": it.spend,
                "clicks": it.clicks,
                "impressions": it.impressions,
                "cpc": it.cpc,
                "ctr": it.ctr,
                "reach": it.reach,
            }
            for it in items
        ]
        stmt = pg_insert(CampaignDailyInsight).values(payload)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_insight_campaign_date",
            set_={
                "spend": stmt.excluded.spend,
                "clicks": stmt.excluded.clicks,
                "impressions": stmt.excluded.impressions,
                "cpc": stmt.excluded.cpc,
                "ctr": stmt.excluded.ctr,
                "reach": stmt.excluded.reach,
                "fb_campaign_id": stmt.excluded.fb_campaign_id,
                "updated_at": func.now(),
            },
        )
        self.db.execute(stmt)
        self.db.flush()
        return len(payload)

    def rebuild_ad_spend_from_meta(self, user_id: int) -> int:
        """Projeta o GASTO e os CLIQUES do Meta na tabela AdSpend (fonte do Dashboard).

        Agrega por (dia, sub_id da campanha vinculada) a partir de TODOS os
        CampaignDailyInsight do banco (não da resposta da API — robusto a falha transitória).
        Regras (rodada 5 / frente C):
        - O Meta é AUTORITATIVO nos dias que cobre: substitui o manual daqueles dias
          (preservado no backup ad_spends_manual_backup, migration 028).
        - O manual de dias ANTERIORES à cobertura do Meta PERMANECE (não zera o que o
          pessoal já usava). Sem dupla contagem (1 origem por dia coberto).
        - Inclui gasto/cliques SEM vínculo (sub_id NULL) → contam no total do Dashboard.
        Idempotente: re-rodar reconstrói só as linhas source='meta'.
        """
        agg = (
            self.db.query(
                CampaignDailyInsight.date,
                Campaign.sub_id,
                func.sum(CampaignDailyInsight.spend).label("spend"),
                func.sum(CampaignDailyInsight.clicks).label("clicks"),
            )
            .join(Campaign, Campaign.id == CampaignDailyInsight.campaign_id)
            .filter(CampaignDailyInsight.user_id == user_id)
            .group_by(CampaignDailyInsight.date, Campaign.sub_id)
            .all()
        )
        covered_dates = {row[0] for row in agg}

        # Limpa a projeção Meta anterior (idempotência).
        self.db.query(AdSpend).filter(
            AdSpend.user_id == user_id, AdSpend.source == "meta"
        ).delete(synchronize_session=False)
        # Meta substitui o manual SÓ nos dias que cobre; manual de dias anteriores fica.
        if covered_dates:
            self.db.query(AdSpend).filter(
                AdSpend.user_id == user_id,
                AdSpend.source != "meta",
                AdSpend.date.in_(covered_dates),
            ).delete(synchronize_session=False)
        self.db.flush()

        new_rows = [
            AdSpend(
                user_id=user_id,
                date=d,
                sub_id=sub_id,
                amount=float(spend or 0.0),
                clicks=int(clicks or 0),
                source="meta",
            )
            for (d, sub_id, spend, clicks) in agg
            if (spend or 0) > 0 or (clicks or 0) > 0
        ]
        if new_rows:
            self.db.add_all(new_rows)
            self.db.flush()
        return len(new_rows)

    def list_insights(
        self,
        user_id: int,
        campaign_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[CampaignDailyInsight]:
        query = (
            self.db.query(CampaignDailyInsight)
            .filter(CampaignDailyInsight.user_id == user_id)
        )
        if campaign_id is not None:
            query = query.filter(CampaignDailyInsight.campaign_id == campaign_id)
        if start_date:
            query = query.filter(CampaignDailyInsight.date >= start_date)
        if end_date:
            query = query.filter(CampaignDailyInsight.date <= end_date)
        return query.order_by(CampaignDailyInsight.date.desc()).all()

    # ------------------- agregação de comissões (DatasetRow) ------------------- #

    def aggregate_by_subids(
        self,
        user_id: int,
        sub_ids: List[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[str, dict]:
        """Agrega comissão/faturamento/pedidos de DatasetRow por sub_id1.

        Retorna { sub_id: {commission, revenue, orders, direct_orders} }.
        """
        if not sub_ids:
            return {}

        norm = _norm_sub_id()
        base = self.db.query(DatasetRow).filter(
            DatasetRow.user_id == user_id,
            norm.in_(sub_ids),
        )
        if start_date:
            base = base.filter(DatasetRow.date >= start_date)
        if end_date:
            base = base.filter(DatasetRow.date <= end_date)

        # Agregados de valor + total de pedidos distintos por sub_id (normalizado).
        rows = (
            base.with_entities(
                norm.label("sub_id"),
                func.coalesce(func.sum(DatasetRow.commission), 0.0).label("commission"),
                func.coalesce(func.sum(DatasetRow.revenue), 0.0).label("revenue"),
                func.count(distinct(DatasetRow.order_id)).label("orders"),
            )
            .group_by(norm)
            .all()
        )

        # Pedidos diretos (attributionType = mesma loja do clique) por sub_id.
        direct_rows = (
            base.filter(DatasetRow.attribution_type == DIRECT_ATTRIBUTION_TYPE)
            .with_entities(
                norm.label("sub_id"),
                func.count(distinct(DatasetRow.order_id)).label("direct_orders"),
            )
            .group_by(norm)
            .all()
        )
        direct_map = {r.sub_id: int(r.direct_orders or 0) for r in direct_rows}

        result: Dict[str, dict] = {}
        for r in rows:
            result[r.sub_id] = {
                "commission": float(r.commission or 0.0),
                "revenue": float(r.revenue or 0.0),
                "orders": int(r.orders or 0),
                "direct_orders": direct_map.get(r.sub_id, 0),
            }
        return result

    def daily_by_subid(
        self,
        user_id: int,
        sub_id: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Dict[date, dict]:
        """Agrega comissão/faturamento/pedidos por dia para um único sub_id."""
        base = self.db.query(DatasetRow).filter(
            DatasetRow.user_id == user_id,
            _norm_sub_id() == sub_id,
        )
        if start_date:
            base = base.filter(DatasetRow.date >= start_date)
        if end_date:
            base = base.filter(DatasetRow.date <= end_date)

        rows = (
            base.with_entities(
                DatasetRow.date.label("date"),
                func.coalesce(func.sum(DatasetRow.commission), 0.0).label("commission"),
                func.coalesce(func.sum(DatasetRow.revenue), 0.0).label("revenue"),
                func.count(distinct(DatasetRow.order_id)).label("orders"),
            )
            .group_by(DatasetRow.date)
            .all()
        )
        return {
            r.date: {
                "commission": float(r.commission or 0.0),
                "revenue": float(r.revenue or 0.0),
                "orders": int(r.orders or 0),
            }
            for r in rows
        }

    def sub_id_sales_summary(self, user_id: int) -> List[dict]:
        """Todos os sub_ids do usuário que JÁ tiveram venda (histórico, sem recorte de período).

        Retorna [{sub_id, orders, commission}] — usado pelo modal de vínculo.
        """
        norm = _norm_sub_id()
        rows = (
            self.db.query(
                norm.label("sub_id"),
                func.coalesce(func.sum(DatasetRow.commission), 0.0).label("commission"),
                func.count(distinct(DatasetRow.order_id)).label("orders"),
            )
            .filter(
                DatasetRow.user_id == user_id,
                DatasetRow.sub_id1.isnot(None),
                norm != "",
            )
            .group_by(norm)
            .all()
        )
        return [
            {
                "sub_id": r.sub_id,
                "orders": int(r.orders or 0),
                "commission": float(r.commission or 0.0),
            }
            for r in rows
        ]
