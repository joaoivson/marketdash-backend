from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FacebookOAuthUrlResponse(BaseModel):
    url: str


class FacebookOAuthCallback(BaseModel):
    code: str
    # Opcional: se ausente, usa FACEBOOK_OAUTH_REDIRECT_URI do servidor.
    redirect_uri: Optional[str] = None
    state: Optional[str] = None


class FacebookAdAccount(BaseModel):
    account_id: str
    name: Optional[str] = None
    currency: Optional[str] = None
    account_status: Optional[int] = None
    # id completo no formato "act_<account_id>" para uso nas chamadas seguintes
    id: Optional[str] = None


class FacebookAdAccountSelect(BaseModel):
    ad_account_id: str
    ad_account_name: Optional[str] = None


class FacebookIntegrationResponse(BaseModel):
    id: int
    user_id: int
    fb_user_name: Optional[str] = None
    ad_account_id: Optional[str] = None
    ad_account_name: Optional[str] = None
    is_active: bool
    last_sync_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
