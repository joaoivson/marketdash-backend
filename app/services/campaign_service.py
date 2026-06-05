import logging
from datetime import date
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.repositories.campaign_repository import CampaignRepository
from app.schemas.campaign import (
    CampaignDailyPoint,
    CampaignDetailResponse,
    CampaignKPIs,
    CampaignListResponse,
    CampaignMetrics,
    CampaignResponse,
)

logger = logging.getLogger(__name__)

# Limiar de ROAS para classificar a saúde da campanha (ver faixa de cor no mockup).
ROAS_HEALTHY = 1.5
ROAS_BREAKEVEN = 1.0


def _is_active(campaign: Campaign) -> bool:
    eff = (campaign.effective_status or campaign.status or "").upper()
    return eff == "ACTIVE"


def _health(linked: bool, spend: float, roas: float) -> str:
    if not linked:
        return "unlinked"
    if spend <= 0:
        return "healthy"
    if roas < ROAS_BREAKEVEN:
        return "loss"
    if roas < ROAS_HEALTHY:
        return "warning"
    return "healthy"


def _compute_metrics(spend: float, clicks: int, impressions: int, comm: dict) -> CampaignMetrics:
    commission = comm.get("commission", 0.0)
    revenue = comm.get("revenue", 0.0)
    orders = comm.get("orders", 0)
    direct_orders = comm.get("direct_orders", 0)
    return CampaignMetrics(
        spend=round(spend, 2),
        clicks=clicks,
        impressions=impressions,
        cpc=round(spend / clicks, 2) if clicks > 0 else None,
        ctr=round(clicks / impressions * 100, 2) if impressions > 0 else None,
        commission=round(commission, 2),
        revenue=round(revenue, 2),
        orders=orders,
        direct_orders=direct_orders,
        profit=round(commission - spend, 2),
        roas=round(revenue / spend, 2) if spend > 0 else 0.0,
    )


class CampaignService:
    def __init__(self, repo: CampaignRepository):
        self.repo = repo
        self.db: Session = repo.db

    def _build_response(self, campaign: Campaign, spend: float, clicks: int, impressions: int, comm: dict) -> CampaignResponse:
        linked = bool(campaign.sub_id)
        metrics = _compute_metrics(spend, clicks, impressions, comm)
        return CampaignResponse(
            id=campaign.id,
            fb_campaign_id=campaign.fb_campaign_id,
            name=campaign.name,
            status=campaign.status,
            effective_status=campaign.effective_status,
            objective=campaign.objective,
            daily_budget=campaign.daily_budget,
            sub_id=campaign.sub_id,
            linked=linked,
            is_active=_is_active(campaign),
            health=_health(linked, metrics.spend, metrics.roas),
            metrics=metrics,
        )

    def list_campaigns(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        only_active: bool = False,
        search: Optional[str] = None,
    ) -> CampaignListResponse:
        campaigns = self.repo.list_by_user(user_id)

        if search:
            term = search.strip().lower()
            campaigns = [c for c in campaigns if term in (c.name or "").lower()]
        if only_active:
            campaigns = [c for c in campaigns if _is_active(c)]

        # Insights agregados por campaign_id no período.
        insights = self.repo.list_insights(user_id, start_date=start_date, end_date=end_date)
        spend_map: dict[int, dict] = {}
        for ins in insights:
            agg = spend_map.setdefault(ins.campaign_id, {"spend": 0.0, "clicks": 0, "impressions": 0})
            agg["spend"] += ins.spend or 0.0
            agg["clicks"] += ins.clicks or 0
            agg["impressions"] += ins.impressions or 0

        # Comissões agregadas pelos sub_ids vinculados.
        linked_sub_ids = [c.sub_id for c in campaigns if c.sub_id]
        comm_map = self.repo.aggregate_by_subids(user_id, linked_sub_ids, start_date, end_date)

        responses: List[CampaignResponse] = []
        for c in campaigns:
            agg = spend_map.get(c.id, {"spend": 0.0, "clicks": 0, "impressions": 0})
            comm = comm_map.get(c.sub_id, {}) if c.sub_id else {}
            responses.append(self._build_response(c, agg["spend"], agg["clicks"], agg["impressions"], comm))

        # Ordena: pior ROAS primeiro (igual ao mockup desktop), não vinculadas no topo.
        responses.sort(key=lambda r: (r.linked, r.metrics.roas if r.metrics.spend > 0 else float("inf")))

        kpis = self._compute_kpis(responses)
        return CampaignListResponse(kpis=kpis, campaigns=responses)

    def _compute_kpis(self, responses: List[CampaignResponse]) -> CampaignKPIs:
        total_spend = sum(r.metrics.spend for r in responses)
        total_clicks = sum(r.metrics.clicks for r in responses)
        total_commission = sum(r.metrics.commission for r in responses)
        total_revenue = sum(r.metrics.revenue for r in responses)
        total_daily_budget = sum(r.daily_budget or 0.0 for r in responses if r.is_active)
        return CampaignKPIs(
            avg_cpc=round(total_spend / total_clicks, 2) if total_clicks > 0 else None,
            total_spend=round(total_spend, 2),
            total_commission=round(total_commission, 2),
            total_profit=round(total_commission - total_spend, 2),
            avg_roas=round(total_revenue / total_spend, 2) if total_spend > 0 else 0.0,
            total_daily_budget=round(total_daily_budget, 2),
        )

    def get_detail(
        self,
        user_id: int,
        campaign_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> CampaignDetailResponse:
        campaign = self.repo.get_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")

        insights = self.repo.list_insights(user_id, campaign_id=campaign_id, start_date=start_date, end_date=end_date)
        total_spend = sum(i.spend or 0.0 for i in insights)
        total_clicks = sum(i.clicks or 0 for i in insights)
        total_impr = sum(i.impressions or 0 for i in insights)

        comm = (
            self.repo.aggregate_by_subids(user_id, [campaign.sub_id], start_date, end_date).get(campaign.sub_id, {})
            if campaign.sub_id else {}
        )
        response = self._build_response(campaign, total_spend, total_clicks, total_impr, comm)

        # Dia a dia: combina insights (FB) com comissões (DatasetRow) por data.
        daily_comm = self.repo.daily_by_subid(user_id, campaign.sub_id, start_date, end_date) if campaign.sub_id else {}
        daily: List[CampaignDailyPoint] = []
        for ins in sorted(insights, key=lambda i: i.date, reverse=True):
            c = daily_comm.get(ins.date, {})
            spend = ins.spend or 0.0
            commission = c.get("commission", 0.0)
            revenue = c.get("revenue", 0.0)
            daily.append(
                CampaignDailyPoint(
                    date=ins.date,
                    spend=round(spend, 2),
                    clicks=ins.clicks or 0,
                    impressions=ins.impressions or 0,
                    cpc=round(spend / ins.clicks, 2) if ins.clicks else None,
                    ctr=ins.ctr,
                    commission=round(commission, 2),
                    revenue=round(revenue, 2),
                    orders=c.get("orders", 0),
                    profit=round(commission - spend, 2),
                    roas=round(revenue / spend, 2) if spend > 0 else 0.0,
                )
            )

        return CampaignDetailResponse(campaign=response, daily=daily)

    def set_link(self, user_id: int, campaign_id: int, sub_id: Optional[str]) -> CampaignResponse:
        campaign = self.repo.get_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")
        normalized = sub_id.strip() if sub_id and sub_id.strip() else None
        self.repo.set_sub_id(campaign, normalized)
        self.db.commit()
        self.db.refresh(campaign)
        # Retorna sem métricas pesadas recalculadas — frontend recarrega a lista.
        return self._build_response(campaign, 0.0, 0, 0, {})
