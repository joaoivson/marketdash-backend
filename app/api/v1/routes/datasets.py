from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.schemas.dataset import DatasetResponse, DatasetRowResponse, AdSpendResponse
from app.services.dataset_service import DatasetService

router = APIRouter(tags=["datasets"])


class AdSpendPayload(BaseModel):
    amount: float = Field(..., gt=0, description="Valor investido em anúncios")
    sub_id1: str | None = Field(None, description="Sub_id1 opcional para associar o gasto")


@router.post("/upload", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    file_content = await file.read()
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.upload_csv(file_content, file.filename, current_user.id)


@router.post("/latest/ad_spend", response_model=AdSpendResponse)
def set_ad_spend(
    payload: AdSpendPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.apply_ad_spend(current_user.id, payload.amount, payload.sub_id1, db)


@router.get("/latest/rows", response_model=List[DatasetRowResponse])
def list_latest_rows(
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    include_raw_data: bool = Query(True, description="Incluir campo raw_data na resposta"),
    limit: int | None = Query(None, ge=1, description="Quantidade máxima de linhas (opcional)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_latest_rows(current_user.id, start_date, end_date, include_raw_data, limit, offset)


@router.get("/all/rows", response_model=List[DatasetRowResponse])
def list_all_rows(
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    include_raw_data: bool = Query(True, description="Incluir campo raw_data na resposta"),
    limit: int | None = Query(None, ge=1, description="Quantidade máxima de linhas (opcional)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_all_rows(current_user.id, start_date, end_date, include_raw_data, limit, offset)


@router.get("", response_model=List[DatasetResponse])
def list_datasets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_datasets(current_user.id)


@router.delete("/all", status_code=status.HTTP_200_OK)
def delete_all_datasets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Deleta todos os datasets do usuário autenticado."""
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.delete_all(current_user.id)


@router.get("/{dataset_id}/rows", response_model=List[DatasetRowResponse])
def list_dataset_rows(
    dataset_id: int,
    start_date: date = Query(..., description="Data inicial (obrigatória)"),
    end_date: date = Query(..., description="Data final (obrigatória)"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    # O service já valida se o dataset pertence ao usuário (filtra por user_id primeiro)
    return service.list_dataset_rows(dataset_id, current_user.id, start_date, end_date, True)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    # O service já valida se o dataset pertence ao usuário (filtra por user_id primeiro)
    return service.get_dataset(dataset_id, current_user.id)


@router.post("/{dataset_id}/refresh", response_model=DatasetResponse)
async def refresh_dataset(
    dataset_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    # O service já valida se o dataset pertence ao usuário (filtra por user_id primeiro)
    return service.get_dataset(dataset_id, current_user.id)
