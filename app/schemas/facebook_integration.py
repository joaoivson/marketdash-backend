from datetime import datetime
from typing import List, Optional

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


class FacebookAdAccountRef(BaseModel):
    """Referência leve a uma conta selecionada: id 'act_123' + nome de exibição."""
    id: str
    # None = nome desconhecido (ex.: seleção gravada antes da coluna de nomes existir)
    name: Optional[str] = None


class FacebookAdAccountsSelect(BaseModel):
    """Seleção de uma ou mais contas de anúncio (formato 'act_123' ou só o número)."""
    account_ids: List[str]
    # Opcional: id+nome das contas selecionadas, para persistir os nomes e o
    # status não depender da Graph. account_ids segue sendo a fonte da seleção.
    accounts: Optional[List[FacebookAdAccountRef]] = None


class FacebookIntegrationResponse(BaseModel):
    id: int
    user_id: int
    fb_user_name: Optional[str] = None
    ad_account_id: Optional[str] = None
    ad_account_name: Optional[str] = None
    # Contas selecionadas (preenchido pelo service a partir de account_ids_list()).
    ad_account_ids: List[str] = []
    # Contas selecionadas com nome persistido (name None quando desconhecido).
    ad_accounts: List[FacebookAdAccountRef] = []
    is_active: bool
    # conectado | nunca | desconectado
    connection_state: str = "nunca"
    last_sync_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ClearAdsDataRequest(BaseModel):
    confirm: bool = False
