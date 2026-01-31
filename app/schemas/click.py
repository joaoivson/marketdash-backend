from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional, Any


class ClickRowBase(BaseModel):
    date: date
    time: Optional[str] = None
    channel: str
    sub_id: Optional[str] = None
    clicks: int
    raw_data: Optional[Any] = None


class ClickRowResponse(ClickRowBase):
    id: int
    dataset_id: int
    user_id: int

    class Config:
        from_attributes = True
