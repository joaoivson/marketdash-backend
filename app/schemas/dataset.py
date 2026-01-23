from pydantic import BaseModel
from datetime import datetime, date, time
from typing import Optional, Any


class DatasetBase(BaseModel):
    filename: str


class DatasetCreate(DatasetBase):
    pass


class DatasetResponse(DatasetBase):
    id: int
    user_id: int
    uploaded_at: datetime

    class Config:
        from_attributes = True


class DatasetRowBase(BaseModel):
    date: date
    time: Optional[time] = None
    transaction_date: Optional[date] = None
    product: str
    product_name: Optional[str] = None
    platform: Optional[str] = None
    revenue: Optional[float] = None
    cost: Optional[float] = None
    commission: Optional[float] = None
    gross_value: Optional[float] = None
    commission_value: Optional[float] = None
    net_value: Optional[float] = None
    quantity: Optional[int] = None
    status: Optional[str] = None
    category: Optional[str] = None
    sub_id1: Optional[str] = None
    mes_ano: Optional[str] = None
    raw_data: Optional[Any] = None


class DatasetRowCreate(DatasetRowBase):
    profit: float


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
