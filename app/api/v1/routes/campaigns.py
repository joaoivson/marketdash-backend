import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    only_active: bool = Query(default=False),
    search: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista campanhas com KPIs e métricas agregadas no período."""
    return _service(db).list_campaigns(
        current_user.id, start_date=start_date, end_date=end_date, only_active=only_active, search=search
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


@router.patch("/{campaign_id}/link", response_model=CampaignResponse)
def link_campaign(
    campaign_id: int,
    payload: CampaignLinkUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Vincula (ou desvincula, sub_id=null) a campanha a um Sub ID da Shopee."""
    return _service(db).set_link(current_user.id, campaign_id, payload.sub_id)


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
