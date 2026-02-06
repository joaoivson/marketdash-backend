from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_active_subscription
from app.db.session import get_db
from app.models.dataset import Dataset
from app.models.user import User
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.schemas.click import ClickRowResponse, ClickTaskResponse
from app.services.click_service import ClickService
from app.tasks.csv_tasks import process_click_csv_task

router = APIRouter(tags=["clicks"])


@router.post("/upload", response_model=ClickTaskResponse, status_code=status.HTTP_201_CREATED)
async def upload_click_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Enfileira processamento de CSV de cliques via Celery; retorna task_id e dataset_id para polling em GET /datasets/{dataset_id}/status."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

    file_content = await file.read()
    dataset_repo = DatasetRepository(db)
    dataset = dataset_repo.create(
        Dataset(user_id=current_user.id, filename=file.filename, type="click", status="pending")
    )
    db.commit()
    db.refresh(dataset)

    task = process_click_csv_task.delay(dataset.id, current_user.id, file_content, file.filename)

    return {
        "task_id": task.id,
        "dataset_id": dataset.id,
        "status": "pending",
    }


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
