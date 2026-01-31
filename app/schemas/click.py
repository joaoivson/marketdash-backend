from pydantic import BaseModel
from datetime import datetime, date
from typing import Optional, Any


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
