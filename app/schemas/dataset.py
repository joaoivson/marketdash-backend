from pydantic import BaseModel, Field
from datetime import datetime, date
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
