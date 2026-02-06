import base64
from datetime import date
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import redis.exceptions
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_active_subscription
from app.core.config import settings
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
    """Processa CSV de cliques. Se PROCESS_CSV_SYNC=true, processa na requisição e retorna status completed; senão enfileira via Celery (pending)."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

    dataset_repo = DatasetRepository(db)
    dataset = dataset_repo.create(
        Dataset(user_id=current_user.id, filename=file.filename, type="click", status="pending")
    )
    db.commit()
    db.refresh(dataset)

    if settings.PROCESS_CSV_SYNC:
        if settings.UPLOAD_TEMP_DIR:
            path = Path(settings.UPLOAD_TEMP_DIR) / f"{dataset.id}_{uuid4().hex}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
            file_content = path.read_bytes()
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        else:
            file_content = await file.read()
        click_service = ClickService(dataset_repo, ClickRowRepository(db))
        click_service.process_click_csv(dataset.id, current_user.id, file_content, file.filename)
        db.refresh(dataset)
        return {
            "task_id": f"sync-{dataset.id}",
            "dataset_id": dataset.id,
            "status": "completed",
        }

    try:
        if settings.UPLOAD_TEMP_DIR:
            path = Path(settings.UPLOAD_TEMP_DIR) / f"{dataset.id}_{uuid4().hex}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "wb") as f:
                while chunk := await file.read(1024 * 1024):
                    f.write(chunk)
            task = process_click_csv_task.delay(
                dataset.id, current_user.id, file.filename,
                file_path=str(path), file_content_b64=None,
            )
        else:
            file_content = await file.read()
            task = process_click_csv_task.delay(
                dataset.id, current_user.id, file.filename,
                file_path=None, file_content_b64=base64.b64encode(file_content).decode("utf-8"),
            )
    except redis.exceptions.AuthenticationError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis: autenticação inválida. Configure REDIS_PASSWORD no ambiente com a senha do Redis, ou use REDIS_URL no formato redis://:SENHA@host:6379/0.",
        )
    except RuntimeError as e:
        if "reconnect" in str(e).lower() and "redis" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Redis: falha de conexão/autenticação. Verifique REDIS_URL e REDIS_PASSWORD no ambiente e reinicie a aplicação.",
            )
        raise

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
