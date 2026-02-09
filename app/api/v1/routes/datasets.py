import base64
from datetime import date
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

import redis.exceptions
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.schemas.dataset import DatasetResponse, DatasetRowResponse, AdSpendResponse, DatasetTaskResponse
from app.services.dataset_service import DatasetService
from app.tasks.csv_tasks import process_csv_task

router = APIRouter(tags=["datasets"])


class AdSpendPayload(BaseModel):
    amount: float = Field(..., gt=0, description="Valor investido em anúncios")
    sub_id1: str | None = Field(None, description="Sub_id1 opcional para associar o gasto")


@router.post("/upload", response_model=DatasetTaskResponse, status_code=status.HTTP_201_CREATED)
async def upload_csv(
    file: UploadFile = File(...),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Processa CSV de comissão/vendas. Se PROCESS_CSV_SYNC=true, processa na requisição e retorna status completed; senão enfileira via Celery (pending)."""
    if not file.filename.endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    dataset = service.create_dataset(current_user.id, file.filename)
    db.commit()
    db.refresh(dataset)

    if settings.PROCESS_CSV_SYNC:
        # Processamento síncrono: sem Celery; dados disponíveis logo após o upload (útil quando não há worker).
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
        service.process_commission_csv(dataset.id, current_user.id, file_content, file.filename)
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
            size = path.stat().st_size
            if size <= settings.UPLOAD_INLINE_MAX_BYTES:
                file_content = path.read_bytes()
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass
                task = process_csv_task.delay(
                    dataset.id, current_user.id, file.filename,
                    file_path=None, file_content_b64=base64.b64encode(file_content).decode("utf-8"),
                )
            else:
                task = process_csv_task.delay(
                    dataset.id, current_user.id, file.filename,
                    file_path=str(path), file_content_b64=None,
                )
        else:
            file_content = await file.read()
            task = process_csv_task.delay(
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


@router.get("/{dataset_id}/status", response_model=DatasetResponse)
def get_dataset_status(
    dataset_id: int,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.get_dataset(dataset_id, current_user.id)


@router.post("/latest/ad_spend", response_model=AdSpendResponse)
def set_ad_spend(
    payload: AdSpendPayload,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.apply_ad_spend(current_user.id, payload.amount, payload.sub_id1, db)


@router.get("/latest/rows", response_model=List[DatasetRowResponse])
def list_latest_rows(
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    limit: int | None = Query(None, ge=1, description="Quantidade máxima de linhas (opcional)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_latest_rows(current_user.id, start_date, end_date, limit, offset)


@router.get("/all/rows", response_model=List[DatasetRowResponse])
def list_all_rows(
    start_date: date | None = Query(None, description="Data inicial (opcional)"),
    end_date: date | None = Query(None, description="Data final (opcional)"),
    limit: int | None = Query(None, ge=1, description="Quantidade máxima de linhas (opcional)"),
    offset: int = Query(0, ge=0, description="Deslocamento para paginação"),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_all_rows(current_user.id, start_date, end_date, limit, offset)


@router.get("", response_model=List[DatasetResponse])
def list_datasets(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    return service.list_datasets(current_user.id)


@router.delete("/all", status_code=status.HTTP_200_OK)
def delete_all_datasets(
    current_user: User = Depends(require_active_subscription),
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
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    # O service já valida se o dataset pertence ao usuário (filtra por user_id primeiro)
    return service.list_dataset_rows(dataset_id, current_user.id, start_date, end_date)


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(
    dataset_id: int,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    # O service já valida se o dataset pertence ao usuário (filtra por user_id primeiro)
    return service.get_dataset(dataset_id, current_user.id)


@router.post("/{dataset_id}/refresh", response_model=DatasetResponse)
async def refresh_dataset(
    dataset_id: int,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    service = DatasetService(DatasetRepository(db), DatasetRowRepository(db))
    # O service já valida se o dataset pertence ao usuário (filtra por user_id primeiro)
    return service.get_dataset(dataset_id, current_user.id)
