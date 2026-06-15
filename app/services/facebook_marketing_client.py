"""Cliente HTTP para a Facebook Marketing API (Graph API).

Espelha o estilo de shopee_graphql_client: funções async, tratamento de erro
uniforme que levanta HTTPException. Cobre OAuth (troca de code → token
long-lived), leitura (ad accounts, campaigns, insights) e escrita
(status e orçamento de campanha).

Nota sobre orçamento: a Graph API representa `daily_budget`/`lifetime_budget`
na menor unidade da moeda da conta (centavos para BRL). Internamente o
MarketDash trabalha em BRL, então convertemos dividindo/multiplicando por 100.
"""

import logging
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0
OAUTH_DIALOG_BASE = "https://www.facebook.com"
GRAPH_BASE = "https://graph.facebook.com"

# Permissões pedidas no OAuth.
DEFAULT_SCOPES = ["ads_read", "ads_management"]


def _api_version() -> str:
    return settings.FACEBOOK_API_VERSION or "v21.0"


def _graph_url(path: str) -> str:
    return f"{GRAPH_BASE}/{_api_version()}/{path.lstrip('/')}"


def _require_app_credentials() -> tuple[str, str]:
    if not settings.FACEBOOK_APP_ID or not settings.FACEBOOK_APP_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Integração Facebook não configurada no servidor (FACEBOOK_APP_ID/SECRET ausentes).",
        )
    return settings.FACEBOOK_APP_ID, settings.FACEBOOK_APP_SECRET


# --------------------------------------------------------------------------- #
#  HTTP helpers                                                               #
# --------------------------------------------------------------------------- #


def _raise_graph_error(body: dict, http_status: int) -> None:
    """Levanta HTTPException a partir do envelope de erro da Graph API."""
    err = (body or {}).get("error") or {}
    msg = err.get("error_user_msg") or err.get("message") or "Erro desconhecido da API do Facebook."
    code = err.get("code")
    logger.warning("Facebook Graph error code=%s http=%s: %s", code, http_status, msg)
    # 190 = token inválido/expirado → 401 para o frontend reiniciar o OAuth.
    if code == 190:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token do Facebook expirado ou inválido. Reconecte a conta.")
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Facebook: {msg}")


async def _request(method: str, url: str, *, params: Optional[dict] = None, data: Optional[dict] = None) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(method, url, params=params, data=data)
    except httpx.TimeoutException as exc:
        logger.error("Facebook API timeout: %s", exc)
        raise HTTPException(status_code=status.HTTP_504_GATEWAY_TIMEOUT, detail="Timeout ao conectar com a API do Facebook.")
    except httpx.RequestError as exc:
        logger.error("Facebook API request error: %s", exc)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Erro de conexão com a API do Facebook.")

    try:
        body = response.json()
    except Exception:
        body = {}

    if response.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
        _raise_graph_error(body if isinstance(body, dict) else {}, response.status_code)

    return body if isinstance(body, dict) else {"data": body}


async def _get_paginated(url: str, params: dict) -> list[dict]:
    """Segue paginação `paging.next` da Graph API e acumula `data`."""
    out: list[dict] = []
    next_url: Optional[str] = url
    next_params: Optional[dict] = params
    page = 0
    while next_url and page < 50:  # guarda contra loop infinito
        page += 1
        body = await _request("GET", next_url, params=next_params)
        out.extend(body.get("data") or [])
        paging = body.get("paging") or {}
        next_url = paging.get("next")
        next_params = None  # a URL `next` já vem com cursor + token
    return out


# --------------------------------------------------------------------------- #
#  OAuth                                                                       #
# --------------------------------------------------------------------------- #


def build_oauth_url(redirect_uri: str, state: str, scopes: Optional[list[str]] = None) -> str:
    """Monta a URL do diálogo de login do Facebook (frontend redireciona o usuário)."""
    app_id, _ = _require_app_credentials()
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": ",".join(scopes or DEFAULT_SCOPES),
        "response_type": "code",
    }
    return f"{OAUTH_DIALOG_BASE}/{_api_version()}/dialog/oauth?{urlencode(params)}"


async def exchange_code_for_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """Troca o `code` do OAuth por um access token de curta duração."""
    app_id, app_secret = _require_app_credentials()
    params = {
        "client_id": app_id,
        "client_secret": app_secret,
        "redirect_uri": redirect_uri,
        "code": code,
    }
    return await _request("GET", _graph_url("oauth/access_token"), params=params)


async def exchange_for_long_lived_token(short_lived_token: str) -> dict[str, Any]:
    """Troca um token de curta duração por um long-lived (~60 dias)."""
    app_id, app_secret = _require_app_credentials()
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_lived_token,
    }
    return await _request("GET", _graph_url("oauth/access_token"), params=params)


async def get_me(access_token: str) -> dict[str, Any]:
    """Retorna id/name do usuário dono do token."""
    return await _request("GET", _graph_url("me"), params={"fields": "id,name", "access_token": access_token})


# --------------------------------------------------------------------------- #
#  Leitura                                                                     #
# --------------------------------------------------------------------------- #


async def list_ad_accounts(access_token: str) -> list[dict]:
    """Lista as contas de anúncio às quais o token tem acesso."""
    params = {
        "fields": "account_id,name,currency,account_status",
        "access_token": access_token,
        "limit": 200,
    }
    return await _get_paginated(_graph_url("me/adaccounts"), params)


async def list_campaigns(access_token: str, ad_account_id: str) -> list[dict]:
    """Lista campanhas de uma ad account (formato 'act_123')."""
    params = {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget",
        "access_token": access_token,
        "limit": 200,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/campaigns"), params)


async def get_campaign_insights(
    access_token: str,
    campaign_id: str,
    since: str,
    until: str,
) -> list[dict]:
    """Insights diários de uma campanha entre `since` e `until` (YYYY-MM-DD).

    Retorna uma lista de dicts (um por dia) com spend/clicks/impressions/cpc/ctr/reach.
    """
    params = {
        # inline_link_* = métricas de "clique no link" (o que o Gerenciador mostra por padrão
        # e o que importa p/ afiliado: clique que vai pra Shopee). clicks/cpc/ctr "secos" são
        # de TODOS os cliques (curtida, etc.) e ficam acima do real.
        "fields": (
            "spend,clicks,inline_link_clicks,impressions,cpc,cost_per_inline_link_click,"
            "ctr,inline_link_click_ctr,reach,date_start,date_stop"
        ),
        "level": "campaign",
        "time_increment": 1,
        "time_range": '{"since":"%s","until":"%s"}' % (since, until),
        "access_token": access_token,
        "limit": 500,
    }
    return await _get_paginated(_graph_url(f"{campaign_id}/insights"), params)


# --------------------------------------------------------------------------- #
#  Escrita (requer ads_management)                                            #
# --------------------------------------------------------------------------- #


async def update_campaign_status(access_token: str, campaign_id: str, new_status: str) -> dict[str, Any]:
    """Pausa/ativa uma campanha. new_status ∈ {'ACTIVE', 'PAUSED'}."""
    new_status = new_status.upper()
    if new_status not in ("ACTIVE", "PAUSED"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Status deve ser ACTIVE ou PAUSED.")
    data = {"status": new_status, "access_token": access_token}
    return await _request("POST", _graph_url(campaign_id), data=data)


async def update_campaign_daily_budget(access_token: str, campaign_id: str, daily_budget_brl: float) -> dict[str, Any]:
    """Altera o orçamento diário (BRL). Converte para centavos para a Graph API."""
    if daily_budget_brl <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Orçamento deve ser maior que zero.")
    cents = int(round(daily_budget_brl * 100))
    data = {"daily_budget": cents, "access_token": access_token}
    return await _request("POST", _graph_url(campaign_id), data=data)
