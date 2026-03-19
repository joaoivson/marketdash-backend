from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, field_validator


class ShopeeCredentialsUpsert(BaseModel):
    app_id: str
    password: str

    @field_validator("app_id", mode="before")
    @classmethod
    def strip_app_id(cls, v: str) -> str:
        return v.strip()


class ShopeeIntegrationResponse(BaseModel):
    id: int
    user_id: int
    app_id: str
    is_active: bool
    last_sync_at: Optional[datetime]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


class ShopeeGraphQLRequest(BaseModel):
    query: str
    variables: Optional[dict] = None


class ShopeeGraphQLResponse(BaseModel):
    data: Optional[Any] = None
    errors: Optional[list] = None
