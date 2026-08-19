"""Schemas do breakdown de veiculação por plataforma (Instagram vs Facebook)."""

from typing import List, Optional

from pydantic import BaseModel


class PlatformTotals(BaseModel):
    """Totais de uma plataforma no período."""

    platform: str
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0
    # Preenchidos pelo service a partir do vínculo campanha → Sub ID (Shopee).
    commission: float = 0.0
    revenue: float = 0.0
    orders: int = 0
    profit: float = 0.0
    roas: Optional[float] = None
    cpc: Optional[float] = None
    # Fatia do gasto total do período (0..1) — evita o frontend recalcular.
    spend_share: float = 0.0


class PlatformDailyPoint(BaseModel):
    date: str
    platform: str
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0


class PlatformCampaignRow(BaseModel):
    campaign_id: int
    campaign_name: str
    sub_id: Optional[str] = None
    platform: str
    spend: float = 0.0
    clicks: int = 0
    impressions: int = 0


class PlatformBreakdownResponse(BaseModel):
    """Resposta de GET /api/v1/campaigns/platform-breakdown.

    `has_data=False` significa que ainda não houve sync com breakdown por placement
    (integração nova ou primeiro sync ainda rodando) — o frontend mostra o estado
    vazio explicativo em vez de "R$ 0,00", que passaria a impressão errada de que
    o Instagram não gastou nada.
    """

    has_data: bool = False
    totals: List[PlatformTotals] = []
    daily: List[PlatformDailyPoint] = []
    by_campaign: List[PlatformCampaignRow] = []
    total_spend: float = 0.0
