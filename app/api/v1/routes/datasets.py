from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.user import User
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.user_repository import UserRepository
from app.schemas.dataset import DatasetResponse, DatasetRowResponse
from app.services.dataset_service import DatasetService

router = APIRouter(tags=["datasets"])


def get_any_user(db: Session, user_id: int | None = None) -> User:
    repo = UserRepository(db)
    user = repo.get_by_id(user_id) if user_id is not None else repo.get_first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhum usuário encontrado para associar ao dataset")
    return user


class AdSpendPayload(BaseModel):
    amount: float = Field(..., gt=0, description="Valor investido em anúncios")
    sub_id1: str | None = Field(None, description="Sub_id1 opcional para associar o gasto")


@router.post("/upload", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_csv(
    file: UploadFile = File(...),
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    user = get_any_user(db, user_id)
    file_content = await file.read()
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.upload_csv(file_content, file.filename, user.id)


@router.post("/latest/ad_spend")
def set_ad_spend(
    payload: AdSpendPayload,
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    user = get_any_user(db, user_id)
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.apply_ad_spend(user.id, payload.amount, payload.sub_id1, db)


@router.get("/latest/rows", response_model=List[DatasetRowResponse])
def list_latest_rows(
    user_id: int | None = Query(None),
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    include_raw_data: bool = Query(True, description="Incluir campo raw_data na resposta"),
    limit: int | None = Query(None, ge=1, description="Quantidade máxima de linhas (opcional)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    db: Session = Depends(get_db),
):
    user = get_any_user(db, user_id)
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_latest_rows(user.id, start_date, end_date, include_raw_data, limit, offset)


@router.get("/all/rows", response_model=List[DatasetRowResponse])
def list_all_rows(
    user_id: int | None = Query(None),
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    include_raw_data: bool = Query(True, description="Incluir campo raw_data na resposta"),
    limit: int | None = Query(None, ge=1, description="Quantidade máxima de linhas (opcional)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    db: Session = Depends(get_db),
):
    user = get_any_user(db, user_id)
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_all_rows(user.id, start_date, end_date, include_raw_data, limit, offset)


@router.get("", response_model=List[DatasetResponse])
def list_datasets(
    user_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    user = get_any_user(db, user_id)
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_datasets(user.id)


@router.get("/{dataset_id}/rows", response_model=List[DatasetRowResponse])
def list_dataset_rows(
    dataset_id: int,
    start_date: date = Query(..., description="Data inicial (obrigatória)"),
    end_date: date = Query(..., description="Data final (obrigatória)"),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_dataset_rows(dataset_id, start_date, end_date, True)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.get_dataset(dataset_id)


@router.post("/{dataset_id}/refresh", response_model=DatasetResponse)
async def refresh_dataset(dataset_id: int, db: Session = Depends(get_db)):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.get_dataset(dataset_id)
