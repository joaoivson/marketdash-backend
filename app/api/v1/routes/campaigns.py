import io
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from openpyxl import Workbook
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.db.session import get_db
from app.models.user import User
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.schemas.campaign import (
    CampaignBudgetUpdate,
    CampaignDetailResponse,
    CampaignLinkUpdate,
    CampaignListResponse,
    CampaignResponse,
    CampaignStatusUpdate,
    SubIdOptionsResponse,
)
from app.services.campaign_service import CampaignService
from app.services.facebook_integration_service import FacebookIntegrationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["campaigns"])


def _service(db: Session) -> CampaignService:
    return CampaignService(CampaignRepository(db))


def _fb_service(db: Session) -> FacebookIntegrationService:
    return FacebookIntegrationService(FacebookIntegrationRepository(db))


@router.get("", response_model=CampaignListResponse)
def list_campaigns(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    status: str = Query(default="all", description="all | active | paused"),
    search: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista campanhas com KPIs e métricas agregadas no período."""
    return _service(db).list_campaigns(
        current_user.id, start_date=start_date, end_date=end_date, status_filter=status, search=search
    )


# IMPORTANTE: declarar /export ANTES de /{campaign_id}, senão "export" cai no path param int.
@router.get("/export")
def export_campaigns(
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    status: str = Query(default="all", description="all | active | paused"),
    search: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Exporta a lista de campanhas filtrada (período + status) em Excel."""
    data = _service(db).list_campaigns(
        current_user.id, start_date=start_date, end_date=end_date, status_filter=status, search=search
    )

    has_tax = data.has_tax
    wb = Workbook()
    ws = wb.active
    ws.title = "Campanhas"
    ws.append(
        ["Campanha", "Sub ID vinculado", "Status", "Orçamento", "Gasto", "Comissão", "Lucro", "ROAS Real", "CPC", "Pedidos"]
    )
    for c in data.campaigns:
        m = c.metrics
        gasto = m.spend_with_tax if has_tax else m.spend
        comissao = m.commission_net if has_tax else m.commission
        ws.append(
            [
                c.name,
                c.sub_id or "",
                "Ativa" if c.is_active else "Pausada",
                round(c.daily_budget or 0, 2),
                round(gasto, 2),
                round(comissao, 2) if c.linked else 0,
                round(m.profit, 2) if c.linked else 0,
                round(m.roas, 2) if c.linked and m.spend > 0 else 0,
                round(m.cpc, 2) if m.cpc else 0,
                m.orders if c.linked else 0,
            ]
        )
    widths = [46, 22, 10, 11, 12, 12, 12, 9, 9, 9]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    buffer = io.BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = "campanhas.xlsx"
    return Response(
        content=buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"; filename*=UTF-8\'\'{filename}',
            "Access-Control-Expose-Headers": "Content-Disposition, Content-Type",
        },
    )


@router.get("/{campaign_id}", response_model=CampaignDetailResponse)
def get_campaign(
    campaign_id: int,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Detalhe de uma campanha + desempenho dia a dia."""
    return _service(db).get_detail(current_user.id, campaign_id, start_date=start_date, end_date=end_date)


@router.get("/{campaign_id}/sub-id-options", response_model=SubIdOptionsResponse)
def list_sub_id_options(
    campaign_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Sub IDs com histórico de venda para vincular a esta campanha (com sugestões e 1:1)."""
    return _service(db).sub_id_options(current_user.id, campaign_id)


@router.patch("/{campaign_id}/link", response_model=CampaignResponse)
def link_campaign(
    campaign_id: int,
    payload: CampaignLinkUpdate,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vincula (ou desvincula, sub_id=null) a campanha a um Sub ID da Shopee.

    start_date/end_date = recorte da tela, para recalcular as métricas na resposta.
    """
    return _service(db).set_link(
        current_user.id, campaign_id, payload.sub_id, start_date=start_date, end_date=end_date
    )


@router.patch("/{campaign_id}/status", response_model=CampaignResponse)
async def update_status(
    campaign_id: int,
    payload: CampaignStatusUpdate,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Pausa/ativa a campanha no Facebook e reflete o status localmente."""
    repo = CampaignRepository(db)
    campaign = repo.get_by_id(campaign_id, current_user.id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")

    new_status = await _fb_service(db).set_campaign_status(current_user.id, campaign.fb_campaign_id, payload.active)
    campaign.status = new_status
    campaign.effective_status = new_status
    db.commit()
    db.refresh(campaign)
    return _service(db)._build_response(campaign, 0.0, 0, 0, {})


@router.patch("/{campaign_id}/budget", response_model=CampaignResponse)
async def update_budget(
    campaign_id: int,
    payload: CampaignBudgetUpdate,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Altera o orçamento diário da campanha no Facebook e reflete localmente."""
    repo = CampaignRepository(db)
    campaign = repo.get_by_id(campaign_id, current_user.id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")

    new_budget = await _fb_service(db).set_campaign_budget(current_user.id, campaign.fb_campaign_id, payload.daily_budget)
    campaign.daily_budget = new_budget
    db.commit()
    db.refresh(campaign)
    return _service(db)._build_response(campaign, 0.0, 0, 0, {})
