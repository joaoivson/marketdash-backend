from pydantic import BaseModel, Field, field_serializer
from datetime import datetime, date, time
from typing import Optional, Any, List
from app.schemas.dataset import DatasetResponse


class ClickRowBase(BaseModel):
    date: date
    time: Optional[time] = None
    channel: str
    sub_id: Optional[str] = None
    clicks: int

    @field_serializer("date")
    def serialize_date_dd_mm_yyyy(self, d: date) -> str:
        """Exibir data no formato DD-MM-YYYY na API."""
        return d.strftime("%d-%m-%Y") if d else ""

    @field_serializer("time")
    def serialize_time_hh_mm_ss(self, t: Optional[time]) -> Optional[str]:
        """Exibir hora no formato HH:MM:SS na API."""
        return t.strftime("%H:%M:%S") if t else None


class ClickRowResponse(ClickRowBase):
    """Row de clique; id/dataset_id/user_id são None quando a linha é agregada (date, channel)."""
    id: Optional[int] = None
    dataset_id: Optional[int] = None
    user_id: Optional[int] = None

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
