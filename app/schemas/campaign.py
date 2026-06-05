from datetime import date as date_type
from typing import List, Optional

from pydantic import BaseModel


class CampaignDailyPoint(BaseModel):
    """Uma linha do 'dia a dia' de uma campanha, no período filtrado."""
    date: date_type
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0
    cpc: Optional[float] = None
    ctr: Optional[float] = None
    commission: float = 0.0
    revenue: float = 0.0
    orders: int = 0
    profit: float = 0.0
    roas: float = 0.0


class CampaignMetrics(BaseModel):
    """Métricas agregadas de uma campanha no período filtrado.

    Gasto/CPC/CTR/cliques/impressões vêm do Facebook (CampaignDailyInsight).
    Comissão/faturamento/pedidos vêm de DatasetRow via o vínculo sub_id.
    """
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0
    cpc: Optional[float] = None
    ctr: Optional[float] = None
    commission: float = 0.0
    revenue: float = 0.0
    orders: int = 0
    direct_orders: int = 0
    profit: float = 0.0
    roas: float = 0.0


class CampaignResponse(BaseModel):
    id: int
    fb_campaign_id: str
    name: str
    status: Optional[str] = None
    effective_status: Optional[str] = None
    objective: Optional[str] = None
    daily_budget: Optional[float] = None
    sub_id: Optional[str] = None
    linked: bool = False
    is_active: bool = False
    # 'healthy' | 'warning' | 'loss' | 'unlinked'
    health: str = "unlinked"
    metrics: CampaignMetrics


class CampaignKPIs(BaseModel):
    """KPIs do topo da tela Campanhas (agregados do período)."""
    avg_cpc: Optional[float] = None
    total_spend: float = 0.0
    total_commission: float = 0.0
    total_profit: float = 0.0
    avg_roas: float = 0.0
    total_daily_budget: float = 0.0


class CampaignListResponse(BaseModel):
    kpis: CampaignKPIs
    campaigns: List[CampaignResponse]


class CampaignDetailResponse(BaseModel):
    campaign: CampaignResponse
    daily: List[CampaignDailyPoint]


# ------------------------------ updates ------------------------------ #


class CampaignLinkUpdate(BaseModel):
    # NULL/ausente → desvincula. String → vincula ao Sub ID informado.
    sub_id: Optional[str] = None


class CampaignStatusUpdate(BaseModel):
    # True = ACTIVE, False = PAUSED.
    active: bool


class CampaignBudgetUpdate(BaseModel):
    daily_budget: float
