from pydantic import BaseModel, Field, field_serializer
from datetime import datetime, date, time
from typing import Optional


class DatasetBase(BaseModel):
    filename: str


class DatasetCreate(DatasetBase):
    pass


class DatasetResponse(DatasetBase):
    id: int
    user_id: int
    type: str
    status: str
    error_message: Optional[str] = None
    row_count: int = 0
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DatasetUploadResponse(DatasetResponse):
    total_rows: Optional[int] = Field(None, description="Total de linhas (grupos) processadas")
    inserted_rows: Optional[int] = Field(None, description="Número de linhas novas inseridas")
    updated_rows: Optional[int] = Field(None, description="Número de linhas atualizadas (upsert)")
    ignored_rows: Optional[int] = Field(None, description="Número de linhas ignoradas (duplicadas)")


class DatasetTaskResponse(BaseModel):
    task_id: str
    dataset_id: int
    status: str


class DatasetRowBase(BaseModel):
    date: date
    time: Optional[time] = None
    product: str
    platform: Optional[str] = None
    category: Optional[str] = None
    status: Optional[str] = None
    sub_id1: Optional[str] = None
    order_id: Optional[str] = None
    product_id: Optional[str] = None
    revenue: float = 0
    commission: float = 0
    cost: float = 0
    quantity: int = 1

    @field_serializer("date")
    def serialize_date_dd_mm_yyyy(self, d: date) -> str:
        """Exibir data no formato DD-MM-YYYY na API."""
        return d.strftime("%d-%m-%Y") if d else ""

    @field_serializer("time")
    def serialize_time_hh_mm_ss(self, t: Optional[time]) -> Optional[str]:
        """Exibir hora no formato HH:MM:SS na API."""
        return t.strftime("%H:%M:%S") if t else None


class DatasetRowResponse(DatasetRowBase):
    id: int
    dataset_id: int
    user_id: int
    profit: float

    class Config:
        from_attributes = True


class AdSpendResponse(BaseModel):
    updated: int = Field(..., description="Número de linhas atualizadas")
    dataset_id: int = Field(..., description="ID do dataset atualizado")
    sub_id1: Optional[str] = Field(None, description="Sub_id1 usado para filtrar (se aplicável)")
    amount: float = Field(..., description="Valor total aplicado em anúncios")
