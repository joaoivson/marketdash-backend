from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.ad_spend_repository import AdSpendRepository
from app.services.ad_spend_service import AdSpendService

router = APIRouter(tags=["ad_spends"])


class AdSpendCreate(BaseModel):
    date: date
    amount: float = Field(..., gt=0)
    sub_id: Optional[str] = None


class AdSpendUpdate(BaseModel):
    date: Optional[date] = None
    amount: Optional[float] = Field(None, gt=0)
    sub_id: Optional[str] = None


class AdSpendResponse(BaseModel):
    id: int
    date: date
    amount: float
    sub_id: Optional[str]

    class Config:
        from_attributes = True


class BulkAdSpendPayload(BaseModel):
    items: List[AdSpendCreate]


@router.post("", response_model=AdSpendResponse, status_code=status.HTTP_201_CREATED)
def create_ad_spend(
    payload: AdSpendCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    return service.create(current_user.id, payload.date, payload.amount, payload.sub_id)


@router.post("/bulk", response_model=List[AdSpendResponse], status_code=status.HTTP_201_CREATED)
def bulk_create_ad_spend(
    payload: BulkAdSpendPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    return service.bulk_create(current_user.id, payload.items)


@router.get("", response_model=List[AdSpendResponse])
def list_ad_spends(
    start_date: date | None = Query(None),
    end_date: date | None = Query(None),
    limit: int | None = Query(None, ge=1),
    offset: int = Query(0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    return service.list(current_user.id, start_date, end_date, limit, offset)


@router.patch("/{ad_spend_id}", response_model=AdSpendResponse)
def update_ad_spend(
    ad_spend_id: int,
    payload: AdSpendUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    # O service já valida se o ad_spend pertence ao usuário
    return service.update(current_user.id, ad_spend_id, payload)


@router.delete("/{ad_spend_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_ad_spend(
    ad_spend_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = AdSpendService(AdSpendRepository(db))
    # O service já valida se o ad_spend pertence ao usuário
    service.delete(current_user.id, ad_spend_id)
    return None
