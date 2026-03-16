from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class CustomLinkBase(BaseModel):
    name: Optional[str] = None
    original_url: Optional[str] = None
    slug: Optional[str] = None
    tag: Optional[str] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None


class CustomLinkCreate(CustomLinkBase):
    name: str = Field(..., description="Nome do link")
    original_url: str = Field(..., description="URL original de destino")


class CustomLinkUpdate(CustomLinkBase):
    pass


class CustomLinkResponse(CustomLinkBase):
    id: int
    user_id: int
    slug: str
    click_count: int
    is_active: bool
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SlugCheckResponse(BaseModel):
    available: bool
    suggested_slug: str
