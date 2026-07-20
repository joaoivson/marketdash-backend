from pydantic import BaseModel, Field, field_validator
from typing import Optional
from datetime import datetime


class CustomLinkBase(BaseModel):
    name: Optional[str] = None
    original_url: Optional[str] = None
    slug: Optional[str] = None
    tag: Optional[str] = None
    expires_at: Optional[datetime] = None
    is_active: Optional[bool] = None

    # Espaço/quebra de linha colado junto com a URL quebra o redirect na Shopee
    # (ex.: "https://s.shopee.com.br/xxx " → shope.ee/error_page para o comprador).
    @field_validator("name", "original_url", "slug", "tag", mode="before")
    @classmethod
    def _strip_whitespace(cls, v):
        if isinstance(v, str):
            return v.strip()
        return v


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
