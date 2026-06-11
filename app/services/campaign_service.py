import logging
import re
from datetime import date
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.campaign import Campaign
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.user_settings_repository import UserSettingsRepository
from app.services.user_settings_service import UserSettingsService
from app.schemas.campaign import (
    CampaignDailyPoint,
    CampaignDetailResponse,
    CampaignKPIs,
    CampaignListResponse,
    CampaignMetrics,
    CampaignResponse,
    SubIdOption,
    SubIdOptionsResponse,
)

logger = logging.getLogger(__name__)

# Limiar de ROAS para classificar a saúde da campanha (ver faixa de cor no mockup).
ROAS_HEALTHY = 1.5
ROAS_BREAKEVEN = 1.0

# Filtros de status aceitos pela lista (situação real no Meta).
STATUS_FILTERS = ("all", "active", "paused")

# Parte textual de um sub_id (ex.: "legging500280526" -> "legging"), ignorando os números.
_SUBID_WORD_RE = re.compile(r"[a-zA-Z]+")


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


def _compute_metrics(
    spend: float,
    clicks: int,
    impressions: int,
    comm: dict,
    ad_rate: float = 0.0,
    comm_rate: float = 0.0,
) -> CampaignMetrics:
    """ad_rate/comm_rate são FRAÇÕES (ex.: 0.18). Sem imposto = 0 → líquido == bruto."""
    commission = comm.get("commission", 0.0)
    revenue = comm.get("revenue", 0.0)
    orders = comm.get("orders", 0)
    direct_orders = comm.get("direct_orders", 0)

    spend_with_tax = spend * (1 + ad_rate)
    commission_net = commission * (1 - comm_rate)
    profit = commission_net - spend_with_tax
    # ROAS Real do afiliado = comissão líquida / gasto com imposto (breakeven 1.0x).
    # NÃO usa revenue (faturamento), que inflava o ROAS (27x). Comissão é o que entra no bolso.
    roas = round(commission_net / spend_with_tax, 2) if spend_with_tax > 0 else 0.0

    return CampaignMetrics(
        spend=round(spend, 2),
        spend_with_tax=round(spend_with_tax, 2),
        clicks=clicks,
        impressions=impressions,
        cpc=round(spend / clicks, 2) if clicks > 0 else None,
        ctr=round(clicks / impressions * 100, 2) if impressions > 0 else None,
        commission=round(commission, 2),
        commission_net=round(commission_net, 2),
        revenue=round(revenue, 2),
        orders=orders,
        direct_orders=direct_orders,
        profit=round(profit, 2),
        roas=roas,
    )


class CampaignService:
    def __init__(self, repo: CampaignRepository):
        self.repo = repo
        self.db: Session = repo.db

    def _tax_rates(self, user_id: int) -> tuple:
        """Retorna (ad_rate, comm_rate, has_tax) — frações. has_tax=False se ambos 0."""
        s = UserSettingsService(UserSettingsRepository(self.db)).get_settings(user_id)
        ad_rate = (s.get("ad_tax_rate") or 0.0) / 100.0
        comm_rate = (s.get("commission_tax_rate") or 0.0) / 100.0
        return ad_rate, comm_rate, (ad_rate > 0 or comm_rate > 0)

    def _build_response(
        self,
        campaign: Campaign,
        spend: float,
        clicks: int,
        impressions: int,
        comm: dict,
        ad_rate: float = 0.0,
        comm_rate: float = 0.0,
    ) -> CampaignResponse:
        linked = bool(campaign.sub_id)
        metrics = _compute_metrics(spend, clicks, impressions, comm, ad_rate, comm_rate)
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
        status_filter: str = "all",
        search: Optional[str] = None,
    ) -> CampaignListResponse:
        status_filter = status_filter if status_filter in STATUS_FILTERS else "all"
        ad_rate, comm_rate, has_tax = self._tax_rates(user_id)
        campaigns = self.repo.list_by_user(user_id)

        if search:
            term = search.strip().lower()
            campaigns = [c for c in campaigns if term in (c.name or "").lower()]
        if status_filter == "active":
            campaigns = [c for c in campaigns if _is_active(c)]
        elif status_filter == "paused":
            campaigns = [c for c in campaigns if not _is_active(c)]

        # Insights agregados por campaign_id no período.
        insights = self.repo.list_insights(user_id, start_date=start_date, end_date=end_date)
        spend_map: dict[int, dict] = {}
        for ins in insights:
            agg = spend_map.setdefault(ins.campaign_id, {"spend": 0.0, "clicks": 0, "impressions": 0})
            agg["spend"] += ins.spend or 0.0
            agg["clicks"] += ins.clicks or 0
            agg["impressions"] += ins.impressions or 0

        # Comissões agregadas pelos sub_ids vinculados (mesmo recorte de período do gasto).
        linked_sub_ids = [c.sub_id for c in campaigns if c.sub_id]
        comm_map = self.repo.aggregate_by_subids(user_id, linked_sub_ids, start_date, end_date)

        responses: List[CampaignResponse] = []
        for c in campaigns:
            agg = spend_map.get(c.id, {"spend": 0.0, "clicks": 0, "impressions": 0})
            comm = comm_map.get(c.sub_id, {}) if c.sub_id else {}
            # Só entram campanhas com movimentação no período: gasto, entrega (cliques/
            # impressões) ou venda atribuída ao sub_id vinculado. Remove as zeradas.
            has_movement = (
                agg["spend"] > 0
                or agg["clicks"] > 0
                or agg["impressions"] > 0
                or comm.get("orders", 0) > 0
            )
            if not has_movement:
                continue
            responses.append(
                self._build_response(c, agg["spend"], agg["clicks"], agg["impressions"], comm, ad_rate, comm_rate)
            )

        # Ordena por maior gasto no topo. Vínculo não afeta a ordem.
        responses.sort(key=lambda r: r.metrics.spend, reverse=True)

        kpis = self._compute_kpis(responses)
        return CampaignListResponse(kpis=kpis, campaigns=responses, has_tax=has_tax)

    def sub_id_options(self, user_id: int, campaign_id: int) -> SubIdOptionsResponse:
        """Sub IDs com histórico de venda para o modal de vínculo.

        Ordena: primeiro os sugeridos (nome-base bate com o nome da campanha),
        por nº de pedidos desc; depois os demais, também por pedidos desc. Sub IDs
        já vinculados a OUTRA campanha vêm marcados (o frontend bloqueia).
        """
        campaign = self.repo.get_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")

        summary = self.repo.sub_id_sales_summary(user_id)
        linked = self.repo.linked_sub_ids(user_id)
        name_lower = (campaign.name or "").lower()

        options: List[SubIdOption] = []
        for row in summary:
            sub = row["sub_id"]
            match = _SUBID_WORD_RE.match(sub or "")
            word = match.group(0).lower() if match else ""
            suggested = len(word) >= 3 and word in name_lower

            link = linked.get(sub)
            # O vínculo atual da própria campanha não conta como "vinculado a outra".
            if link and link[0] != campaign.id:
                linked_campaign_id, linked_campaign_name = link
            else:
                linked_campaign_id, linked_campaign_name = None, None

            options.append(
                SubIdOption(
                    sub_id=sub,
                    orders=row["orders"],
                    commission=round(row["commission"], 2),
                    suggested=suggested,
                    linked_campaign_id=linked_campaign_id,
                    linked_campaign_name=linked_campaign_name,
                )
            )

        options.sort(key=lambda o: (not o.suggested, -o.orders))
        return SubIdOptionsResponse(options=options)

    def _compute_kpis(self, responses: List[CampaignResponse]) -> CampaignKPIs:
        total_spend = sum(r.metrics.spend for r in responses)
        total_clicks = sum(r.metrics.clicks for r in responses)
        total_commission = sum(r.metrics.commission for r in responses)
        total_spend_with_tax = sum(r.metrics.spend_with_tax for r in responses)
        total_commission_net = sum(r.metrics.commission_net for r in responses)
        total_daily_budget = sum(r.daily_budget or 0.0 for r in responses if r.is_active)
        return CampaignKPIs(
            avg_cpc=round(total_spend / total_clicks, 2) if total_clicks > 0 else None,
            total_spend=round(total_spend, 2),
            total_spend_with_tax=round(total_spend_with_tax, 2),
            total_commission=round(total_commission, 2),
            total_commission_net=round(total_commission_net, 2),
            total_profit=round(total_commission_net - total_spend_with_tax, 2),
            avg_roas=round(total_commission_net / total_spend_with_tax, 2) if total_spend_with_tax > 0 else 0.0,
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

        ad_rate, comm_rate, has_tax = self._tax_rates(user_id)
        insights = self.repo.list_insights(user_id, campaign_id=campaign_id, start_date=start_date, end_date=end_date)
        total_spend = sum(i.spend or 0.0 for i in insights)
        total_clicks = sum(i.clicks or 0 for i in insights)
        total_impr = sum(i.impressions or 0 for i in insights)

        comm = (
            self.repo.aggregate_by_subids(user_id, [campaign.sub_id], start_date, end_date).get(campaign.sub_id, {})
            if campaign.sub_id else {}
        )
        response = self._build_response(campaign, total_spend, total_clicks, total_impr, comm, ad_rate, comm_rate)

        # Dia a dia: combina insights (FB) com comissões (DatasetRow) por data. Já com imposto.
        daily_comm = self.repo.daily_by_subid(user_id, campaign.sub_id, start_date, end_date) if campaign.sub_id else {}
        daily: List[CampaignDailyPoint] = []
        for ins in sorted(insights, key=lambda i: i.date, reverse=True):
            c = daily_comm.get(ins.date, {})
            spend = ins.spend or 0.0
            commission = c.get("commission", 0.0)
            revenue = c.get("revenue", 0.0)
            spend_wt = spend * (1 + ad_rate)
            comm_net = commission * (1 - comm_rate)
            daily.append(
                CampaignDailyPoint(
                    date=ins.date,
                    spend=round(spend, 2),
                    spend_with_tax=round(spend_wt, 2),
                    clicks=ins.clicks or 0,
                    impressions=ins.impressions or 0,
                    cpc=round(spend / ins.clicks, 2) if ins.clicks else None,
                    ctr=ins.ctr,
                    commission=round(commission, 2),
                    commission_net=round(comm_net, 2),
                    revenue=round(revenue, 2),
                    orders=c.get("orders", 0),
                    profit=round(comm_net - spend_wt, 2),
                    roas=round(comm_net / spend_wt, 2) if spend_wt > 0 else 0.0,
                )
            )

        return CampaignDetailResponse(campaign=response, daily=daily, has_tax=has_tax)

    def set_link(
        self,
        user_id: int,
        campaign_id: int,
        sub_id: Optional[str],
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> CampaignResponse:
        campaign = self.repo.get_by_id(campaign_id, user_id)
        if not campaign:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")

        normalized = sub_id.strip() if sub_id and sub_id.strip() else None

        # Vínculo é 1:1 — um Sub ID só pode pertencer a uma campanha.
        if normalized:
            owner = self.repo.find_by_sub_id(user_id, normalized, exclude_campaign_id=campaign_id)
            if owner:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f'O Sub ID "{normalized}" já está vinculado à campanha "{owner.name}".',
                )

        self.repo.set_sub_id(campaign, normalized)
        self.db.commit()
        self.db.refresh(campaign)

        # Recalcula na hora, no MESMO recorte de período da tela: gasto (Facebook) +
        # comissão/pedidos (Shopee via sub_id). Assim a linha já mostra ROAS/lucro corretos.
        insights = self.repo.list_insights(
            user_id, campaign_id=campaign.id, start_date=start_date, end_date=end_date
        )
        spend = sum(i.spend or 0.0 for i in insights)
        clicks = sum(i.clicks or 0 for i in insights)
        impressions = sum(i.impressions or 0 for i in insights)
        comm = (
            self.repo.aggregate_by_subids(user_id, [normalized], start_date, end_date).get(normalized, {})
            if normalized
            else {}
        )
        ad_rate, comm_rate, _ = self._tax_rates(user_id)
        return self._build_response(campaign, spend, clicks, impressions, comm, ad_rate, comm_rate)
