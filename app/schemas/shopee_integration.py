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

    @field_validator("app_id")
    @classmethod
    def app_id_must_be_numeric(cls, v: str) -> str:
        """O AppID da Shopee é numérico (ex.: 18191340007).

        Sem essa checagem dava pra salvar qualquer coisa e a integração ficava
        "conectada" na tela, mas TODA sync falhava com erro genérico da Shopee —
        e o usuário não tinha como saber por quê. Em produção (28/07/2026) 2 das 3
        contas que nunca sincronizaram tinham o e-mail do cliente salvo no lugar
        do AppID.
        """
        if not v:
            raise ValueError("Informe o AppID da Shopee.")
        if not v.isdigit():
            raise ValueError(
                "AppID inválido: use o número do AppID da Shopee (ex.: 18191340007), "
                "não o seu e-mail ou nome de usuário."
            )
        return v


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


class ShopeeSyncRequest(BaseModel):
    """Body opcional para POST /shopee/sync."""

    days: Optional[int] = None
