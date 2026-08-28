"""Cliente HTTP para a Facebook Marketing API (Graph API).

Espelha o estilo de shopee_graphql_client: funções async, tratamento de erro
uniforme que levanta HTTPException. Cobre OAuth (troca de code → token
long-lived), leitura (ad accounts, campaigns, insights) e escrita
(status e orçamento de campanha).

Nota sobre orçamento: a Graph API representa `daily_budget`/`lifetime_budget`
na menor unidade da moeda da conta (centavos para BRL). Internamente o
MarketDash trabalha em BRL, então convertemos dividindo/multiplicando por 100.
"""

import json
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

# Código devolvido no `detail` quando o token do usuário no Facebook não vale mais
# (code 190 da Graph API). A tela usa esse código para oferecer a reconexão.
FACEBOOK_TOKEN_INVALIDO = "facebook_token_invalido"


def _api_version() -> str:
    return settings.FACEBOOK_API_VERSION or "v25.0"


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
    # 190 = token inválido/expirado → o usuário precisa refazer o OAuth.
    #
    # NÃO usar 401 aqui: para o frontend, 401 significa "a SESSÃO do MarketDash morreu" e
    # dispara logout + redirect pro /login. Como esse erro nasce ao abrir Configurações →
    # Facebook (listagem de contas de anúncio), o usuário era expulso da própria tela onde
    # reconectaria a conta — ficando preso sem nunca conseguir consertar.
    # 409 = conflito de estado da integração, tratado pela tela como "reconecte".
    if code == 190:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": FACEBOOK_TOKEN_INVALIDO,
                "message": "Sua conexão com o Facebook expirou. Clique em Conectar com Facebook para reconectar.",
            },
        )
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
    """Monta a URL do diálogo de Login do Facebook para Empresas.

    Apps Business usam `config_id` (não `scope`). Sem FACEBOOK_OAUTH_CONFIG_ID o diálogo
    em Live retorna "Recurso indisponível" para contas externas.

    `config_id` vai logo após `client_id` para aparecer mesmo com a barra truncada.
    """
    app_id, _ = _require_app_credentials()
    config_id = (settings.FACEBOOK_OAUTH_CONFIG_ID or "").strip()
    if not config_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "FACEBOOK_OAUTH_CONFIG_ID não configurado. Crie a configuração em "
                "Login do Facebook para Empresas e defina a variável no servidor."
            ),
        )
    # Login for Business + response_type=code exige override_default_response_type=true
    # Ref: https://developers.facebook.com/docs/facebook-login/facebook-login-for-business/
    # Ordem: client_id → config_id primeiro (visível em screenshots truncados).
    params = {
        "client_id": app_id,
        "config_id": config_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "override_default_response_type": "true",
    }
    # `scopes` mantido na assinatura por compatibilidade; Login for Business ignora scope.
    _ = scopes
    url = f"{OAUTH_DIALOG_BASE}/{_api_version()}/dialog/oauth?{urlencode(params)}"
    logger.info(
        "Facebook OAuth dialog url version=%s config_id=%s redirect_uri=%s",
        _api_version(),
        config_id,
        redirect_uri,
    )
    return url

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


async def list_businesses(access_token: str) -> list[dict]:
    """Lista os Business Manager aos quais o usuário tem acesso."""
    params = {"fields": "id,name", "access_token": access_token, "limit": 200}
    return await _get_paginated(_graph_url("me/businesses"), params)


async def list_business_ad_accounts(access_token: str, business_id: str) -> list[dict]:
    """Lista as contas de anúncio de um Business Manager (próprias + de clientes).

    `/me/adaccounts` só retorna contas com acesso PESSOAL direto do usuário —
    contas geridas via papel no Business Manager (funcionário/parceiro, sem ser
    admin direto na conta) ficam de fora, mesmo com ads_read/ads_management
    concedidos, e nunca aparecem no seletor de Configurações.
    """
    fields = "account_id,name,currency,account_status"
    accounts: list[dict] = []
    for edge in ("owned_ad_accounts", "client_ad_accounts"):
        params = {"fields": fields, "access_token": access_token, "limit": 200}
        try:
            accounts.extend(await _get_paginated(_graph_url(f"{business_id}/{edge}"), params))
        except HTTPException as exc:
            logger.warning(
                "Facebook %s/%s falhou (business_id=%s): %s", business_id, edge, business_id, exc.detail
            )
    return accounts


async def list_ad_accounts(access_token: str) -> list[dict]:
    """Lista as contas de anúncio às quais o token tem acesso.

    Combina `/me/adaccounts` (acesso pessoal direto) com as contas de cada
    Business Manager do usuário — ver `list_business_ad_accounts`.
    """
    params = {
        "fields": "account_id,name,currency,account_status",
        "access_token": access_token,
        "limit": 200,
    }
    accounts = await _get_paginated(_graph_url("me/adaccounts"), params)

    try:
        businesses = await list_businesses(access_token)
    except HTTPException as exc:
        logger.warning("Facebook me/businesses falhou: %s", exc.detail)
        businesses = []

    for business in businesses:
        business_id = business.get("id")
        if business_id:
            accounts.extend(await list_business_ad_accounts(access_token, business_id))

    seen: set[str] = set()
    deduped: list[dict] = []
    for acc in accounts:
        acc_id = acc.get("account_id") or acc.get("id")
        if not acc_id or acc_id in seen:
            continue
        seen.add(acc_id)
        deduped.append(acc)
    return deduped


# Sem filtro, a Graph API omite campanhas ARCHIVED/DELETED da resposta por
# padrão ("A request with no filters returns only campaigns that were not
# archived or deleted" — doc oficial da Meta). Isso fazia o sync nunca mais
# tocar numa campanha depois de arquivada, deixando o último effective_status
# conhecido (tipicamente ACTIVE) congelado pra sempre. Inclui ARCHIVED
# explicitamente; DELETED fica de fora de propósito — campanha deletada não
# é "arquivada", é removida de verdade.
_CAMPAIGN_EFFECTIVE_STATUSES = [
    "ACTIVE", "PAUSED", "ARCHIVED", "PENDING_REVIEW", "DISAPPROVED",
    "PREAPPROVED", "PENDING_BILLING_INFO", "CAMPAIGN_PAUSED",
    "ADSET_PAUSED", "IN_PROCESS", "WITH_ISSUES",
]


async def list_campaigns(access_token: str, ad_account_id: str) -> list[dict]:
    """Lista campanhas de uma ad account (formato 'act_123')."""
    filtering = json.dumps([
        {"field": "effective_status", "operator": "IN", "value": _CAMPAIGN_EFFECTIVE_STATUSES}
    ])
    params = {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget",
        "filtering": filtering,
        "access_token": access_token,
        "limit": 200,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/campaigns"), params)


async def list_ads(access_token: str, ad_account_id: str) -> list[dict]:
    """Lista TODOS os anúncios de uma ad account com o `effective_status` real.

    Reprovação de anúncio (moderação da Meta) NÃO rebaixa o `effective_status`
    da CAMPANHA — ela continua ACTIVE pra sempre, mesmo com o anúncio reprovado
    e zero entrega (`issues_info` no nível da campanha também vem vazio nesse
    caso — testado contra a API real). O status real só existe no ANÚNCIO
    (`DISAPPROVED`, `PENDING_REVIEW`, etc.). Uma chamada por conta (não por
    campanha) evita N+1 — o resultado é agrupado por `campaign_id` no sync.
    """
    params = {
        "fields": "id,campaign_id,effective_status",
        "access_token": access_token,
        "limit": 500,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/ads"), params)


async def get_account_campaign_insights(
    access_token: str,
    ad_account_id: str,
    since: str,
    until: str,
) -> list[dict]:
    """Insights diários de TODAS as campanhas de uma conta — UMA chamada.

    Cada item traz `campaign_id`, então o chamador agrupa por campanha.

    ⚠️ Existia aqui uma versão por campanha (`{campaign_id}/insights`, uma chamada
    para CADA campanha). Com 70+ campanhas por conta e o cron de hora em hora, o
    volume passava de 2.000 chamadas/hora e a Graph API começava a devolver
    `data: []` — HTTP 200, sem erro — para TODAS as campanhas de algumas contas,
    de forma permanente. O gasto sumia da tela em silêncio enquanto esta chamada
    (uma por conta) seguia respondendo normal. Ver CHANGELOG (28/08/2026).

    Não voltar ao modelo por campanha: além do risco, o custo em rate limit crescia
    com o número de campanhas do usuário, e aqui não cresce.
    """
    params = {
        # inline_link_* = métricas de "clique no link" (o que o Gerenciador mostra por padrão
        # e o que importa p/ afiliado: clique que vai pra Shopee). clicks/cpc/ctr "secos" são
        # de TODOS os cliques (curtida, etc.) e ficam acima do real.
        "fields": (
            "campaign_id,spend,clicks,inline_link_clicks,impressions,cpc,"
            "cost_per_inline_link_click,"
            # `actions` traz as conversões contadas pelo Meta; extraímos Lead
            # dali (F7). Campo a mais na MESMA chamada — sem custo extra.
            "ctr,inline_link_click_ctr,reach,actions,date_start,date_stop"
        ),
        "level": "campaign",
        "time_increment": 1,
        "time_range": '{"since":"%s","until":"%s"}' % (since, until),
        "access_token": access_token,
        "limit": 500,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/insights"), params)


# Placements que a Meta pode devolver em `publisher_platform`. A referência de Ads
# Insights não publica o enum (só a de Placement Targeting), então normalizamos e
# mantemos "desconhecido" como escape — descartar a linha faria o gasto por
# plataforma não fechar com o gasto total da campanha.
KNOWN_PUBLISHER_PLATFORMS = {
    "facebook", "instagram", "messenger", "audience_network", "threads",
}
UNKNOWN_PUBLISHER_PLATFORM = "desconhecido"


def normalize_publisher_platform(raw) -> str:
    """Normaliza o valor de `publisher_platform` vindo da Graph API."""
    value = (str(raw or "")).strip().lower()
    if not value:
        return UNKNOWN_PUBLISHER_PLATFORM
    return value if value in KNOWN_PUBLISHER_PLATFORMS else UNKNOWN_PUBLISHER_PLATFORM


async def get_account_campaign_platform_insights(
    access_token: str,
    ad_account_id: str,
    since: str,
    until: str,
) -> list[dict]:
    """Insights diários de TODAS as campanhas de uma conta, quebrados por placement.

    UMA chamada por conta (`level=campaign`), não por campanha — o custo em rate
    limit não cresce com o número de campanhas do usuário.

    `breakdowns=publisher_platform` é compatível com spend/clicks/impressions/cpc/ctr.
    `reach` vem junto mas NÃO é somável entre plataformas (a Meta deduplica por
    período) — quem consome precisa tratar isso.

    Retorna uma lista de dicts com `campaign_id`, `publisher_platform`, `date_start`
    e as métricas.
    """
    params = {
        "fields": (
            "campaign_id,spend,clicks,inline_link_clicks,impressions,cpc,"
            "cost_per_inline_link_click,ctr,inline_link_click_ctr,reach,date_start,date_stop"
        ),
        "level": "campaign",
        "breakdowns": "publisher_platform",
        "time_increment": 1,
        "time_range": '{"since":"%s","until":"%s"}' % (since, until),
        "access_token": access_token,
        "limit": 500,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/insights"), params)


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
