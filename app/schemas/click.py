from pydantic import BaseModel, Field
from datetime import datetime, date
from typing import Optional, Any, List
from app.schemas.dataset import DatasetResponse


class ClickRowBase(BaseModel):
    date: date
    channel: str
    sub_id: Optional[str] = None
    clicks: int


class ClickRowResponse(ClickRowBase):
    id: int
    dataset_id: int
    user_id: int

    class Config:
        from_attributes = True


class ClickListResponse(BaseModel):
    """Resposta das listagens de cliques: total_clicks no nível raiz + rows."""
    total_clicks: int = Field(..., description="Soma de todos os cliques no escopo (para exibir como total)")
    rows: List[ClickRowResponse] = Field(..., description="Lista de registros de cliques")


class ClickUploadResponse(DatasetResponse):
    total_rows: int = Field(..., description="Total de linhas (grupos) processadas")
    inserted_rows: int = Field(..., description="Número de linhas novas inseridas")
    ignored_rows: int = Field(..., description="Número de linhas ignoradas (duplicadas)")


class ClickTaskResponse(BaseModel):
    """Resposta assíncrona do upload de cliques (Celery); usar GET /datasets/{dataset_id}/status para polling."""
    task_id: str
    dataset_id: int
    status: str
