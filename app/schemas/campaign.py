from datetime import date as date_type
from typing import List, Optional

from pydantic import BaseModel


class CampaignDailyPoint(BaseModel):
    """Uma linha do 'dia a dia' de uma campanha, no período filtrado."""
    date: date_type
    spend: float = 0.0               # gasto bruto (sem imposto)
    spend_with_tax: float = 0.0      # gasto com imposto (= spend se sem imposto)
    clicks: int = 0
    impressions: int = 0
    cpc: Optional[float] = None
    ctr: Optional[float] = None
    commission: float = 0.0          # comissão bruta
    commission_net: float = 0.0      # comissão líquida (= commission se sem imposto)
    revenue: float = 0.0
    orders: int = 0
    profit: float = 0.0              # lucro líquido = commission_net - spend_with_tax
    roas: float = 0.0                # ROAS Real = commission_net / spend_with_tax


class CampaignMetrics(BaseModel):
    """Métricas agregadas de uma campanha no período filtrado.

    Gasto/CPC/CTR/cliques/impressões vêm do Facebook (CampaignDailyInsight).
    Comissão/faturamento/pedidos vêm de DatasetRow via o vínculo sub_id.
    """
    spend: float = 0.0               # gasto bruto (sem imposto)
    spend_with_tax: float = 0.0      # gasto com imposto (= spend se sem imposto)
    clicks: int = 0
    impressions: int = 0
    cpc: Optional[float] = None
    ctr: Optional[float] = None
    commission: float = 0.0          # comissão bruta
    commission_net: float = 0.0      # comissão líquida (= commission se sem imposto)
    revenue: float = 0.0
    orders: int = 0
    direct_orders: int = 0
    profit: float = 0.0              # lucro líquido = commission_net - spend_with_tax
    roas: float = 0.0                # ROAS Real = commission_net / spend_with_tax


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
    total_spend: float = 0.0                 # gasto bruto
    total_spend_with_tax: float = 0.0        # gasto com imposto
    total_commission: float = 0.0            # comissão bruta
    total_commission_net: float = 0.0        # comissão líquida
    total_profit: float = 0.0                # lucro líquido
    avg_roas: float = 0.0                     # ROAS Real médio
    total_daily_budget: float = 0.0


class CampaignListResponse(BaseModel):
    kpis: CampaignKPIs
    campaigns: List[CampaignResponse]
    # True quando o usuário cadastrou imposto (front mostra bruto+líquido lado a lado).
    has_tax: bool = False


class CampaignDetailResponse(BaseModel):
    campaign: CampaignResponse
    daily: List[CampaignDailyPoint]
    has_tax: bool = False


class SubIdOption(BaseModel):
    """Um Sub ID com histórico de venda, oferecido no modal de vínculo."""
    sub_id: str
    orders: int = 0
    commission: float = 0.0
    # True quando o nome-base do sub_id aparece no nome da campanha (sugestão).
    suggested: bool = False
    # Preenchidos quando o sub_id já está vinculado a OUTRA campanha (bloqueado).
    linked_campaign_id: Optional[int] = None
    linked_campaign_name: Optional[str] = None


class SubIdOptionsResponse(BaseModel):
    options: List[SubIdOption]


# ------------------------------ updates ------------------------------ #


class CampaignLinkUpdate(BaseModel):
    # NULL/ausente → desvincula. String → vincula ao Sub ID informado.
    sub_id: Optional[str] = None


class CampaignStatusUpdate(BaseModel):
    # True = ACTIVE, False = PAUSED.
    active: bool


class CampaignBudgetUpdate(BaseModel):
    daily_budget: float
