from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_active_subscription
from app.db.session import get_db
from app.models.user import User
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.schemas.dataset import DatasetResponse
from app.schemas.click import ClickRowResponse
from app.services.click_service import ClickService

router = APIRouter(tags=["clicks"])


@router.post("/upload", response_model=DatasetResponse, status_code=status.HTTP_201_CREATED)
async def upload_click_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Realiza o upload de um CSV de cliques e processa os dados."""
    file_content = await file.read()
    service = ClickService(DatasetRepository(db), ClickRowRepository(db))
    return service.upload_click_csv(file_content, file.filename, current_user.id)


@router.get("/latest/rows", response_model=List[ClickRowResponse])
def list_latest_clicks(
    start_date: Optional[date] = Query(None, description="Data inicial"),
    end_date: Optional[date] = Query(None, description="Data final"),
    limit: Optional[int] = Query(None, ge=1, description="Limite de registros"),
    offset: int = Query(0, ge=0, description="Ponto de partida"),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Lista as linhas do último upload de cliques realizado."""
    service = ClickService(DatasetRepository(db), ClickRowRepository(db))
    return service.list_latest_clicks(current_user.id, start_date, end_date, limit, offset)


@router.get("/all/rows", response_model=List[ClickRowResponse])
def list_all_clicks(
    start_date: Optional[date] = Query(None, description="Data inicial"),
    end_date: Optional[date] = Query(None, description="Data final"),
    limit: Optional[int] = Query(None, ge=1, description="Limite de registros"),
    offset: int = Query(0, ge=0, description="Ponto de partida"),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Lista todo o histórico de cliques do usuário."""
    service = ClickService(DatasetRepository(db), ClickRowRepository(db))
    return service.list_all_clicks(current_user.id, start_date, end_date, limit, offset)


@router.delete("/all", status_code=status.HTTP_200_OK)
def delete_all_clicks(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Remove permanentemente todos os dados de cliques do usuário."""
    service = ClickService(DatasetRepository(db), ClickRowRepository(db))
    return service.delete_all_clicks(current_user.id)
