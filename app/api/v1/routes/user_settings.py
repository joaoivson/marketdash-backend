from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.user_settings_repository import UserSettingsRepository
from app.services.user_settings_service import UserSettingsService

router = APIRouter(tags=["settings"])


class UserSettingsResponse(BaseModel):
    ad_tax_rate: float
    commission_tax_rate: float


class UserSettingsUpdate(BaseModel):
    ad_tax_rate: float = Field(..., ge=0.0, le=100.0)
    commission_tax_rate: float = Field(..., ge=0.0, le=100.0)


@router.get("", response_model=UserSettingsResponse)
def get_settings(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = UserSettingsService(UserSettingsRepository(db))
    return service.get_settings(current_user.id)


@router.put("", response_model=UserSettingsResponse)
def update_settings(
    payload: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = UserSettingsService(UserSettingsRepository(db))
    return service.update_settings(current_user.id, payload.ad_tax_rate, payload.commission_tax_rate)
