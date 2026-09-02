"""Cliente da Instagram API com **Instagram Login** (Business Login for Instagram).

Host `graph.instagram.com` e credenciais próprias (`INSTAGRAM_APP_ID/SECRET`), que
NÃO são o App ID/Secret do Facebook. Escolha deliberada (ver
docs/AUTOMACAO_INSTAGRAM.md §3.1):

- não exige Página do Facebook vinculada à conta profissional — o caminho via
  Facebook exige Página e cargo de admin nela, atrito que o público não passa;
- não encosta na configuração de anúncios que já funciona;
- isolamento de risco: permissão do Instagram revogada não derruba o Meta Ads.

Erros são classificados em PERMANENTES (não retentar — retentar queima cota e
reputação do app) e TRANSITÓRIOS (rede/5xx/rate limit).
"""

import logging
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 30.0
OAUTH_AUTHORIZE_BASE = "https://www.instagram.com/oauth/authorize"
OAUTH_TOKEN_URL = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE = "https://graph.instagram.com"

# Nomes ATUAIS dos escopos. Os antigos (business_basic, business_manage_comments,
# business_manage_messages) estão descontinuados e a Meta rejeita o authorize.
DEFAULT_SCOPES = [
    "instagram_business_basic",
    "instagram_business_manage_comments",
    "instagram_business_manage_messages",
]

# Sem este escopo a automação até manda o direct, mas NÃO consegue responder
# publicamente no comentário. Como a tela de consentimento da Meta agrupa
# permissões com nomes próprios, dá pra conceder "só mensagens" sem perceber.
ESCOPO_COMENTARIOS = "instagram_business_manage_comments"
ESCOPO_MENSAGENS = "instagram_business_manage_messages"

# Tipos de conta que a API devolve em `account_type`. PERSONAL não expõe
# comentários nem mensagens — bloqueamos na conexão, com mensagem clara.
TIPOS_PROFISSIONAIS = {"BUSINESS", "MEDIA_CREATOR", "CREATOR"}

# Já respondemos privadamente a este comentário. A Meta permite UMA private reply
# por comentário, para sempre. Retentar é erro garantido e conta contra o app.
ERRO_JA_RESPONDIDO = 2534014


class InstagramApiError(Exception):
    """Erro da API do Instagram, já classificado em permanente × transitório.

    `permanente=True` significa: NÃO retentar. Erro de negócio (já respondido,
    janela expirada, permissão ausente, comentário apagado) não melhora com
    retry — só gasta cota e piora a reputação do app.
    """

    def __init__(
        self,
        mensagem: str,
        *,
        codigo: Optional[int] = None,
        subcodigo: Optional[int] = None,
        http_status: Optional[int] = None,
        permanente: bool = False,
    ):
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.codigo = codigo
        self.subcodigo = subcodigo
        self.http_status = http_status
        self.permanente = permanente

    @property
    def codigo_curto(self) -> str:
        """Identificador curto pra coluna `erro_codigo` de instagram_events."""
        if self.subcodigo:
            return f"{self.codigo or '?'}/{self.subcodigo}"
        if self.codigo:
            return str(self.codigo)
        return str(self.http_status or "erro")


def _api_version() -> str:
    return settings.INSTAGRAM_API_VERSION or "v25.0"


def _graph_url(path: str) -> str:
    return f"{GRAPH_BASE}/{_api_version()}/{path.lstrip('/')}"


def _require_app_credentials() -> tuple[str, str]:
    if not settings.INSTAGRAM_APP_ID or not settings.INSTAGRAM_APP_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Automação do Instagram não configurada no servidor "
                "(INSTAGRAM_APP_ID/INSTAGRAM_APP_SECRET ausentes)."
            ),
        )
    return settings.INSTAGRAM_APP_ID, settings.INSTAGRAM_APP_SECRET


# Códigos que NÃO adiantam retentar.
_CODIGOS_PERMANENTES = {
    10,   # permissão ausente
    100,  # parâmetro inválido (comentário apagado, media_id inexistente)
    190,  # token inválido/expirado — precisa reconectar, não retentar
    200,  # permissão insuficiente
    803,  # objeto não encontrado
}
# Rate limit e sobrecarga: transitórios, valem backoff.
_CODIGOS_TRANSITORIOS = {1, 2, 4, 17, 32, 613}


def _classificar_erro(body: dict, http_status: int) -> InstagramApiError:
    err = (body or {}).get("error") or {}
    msg = (
        err.get("error_user_msg")
        or err.get("message")
        or f"Erro da API do Instagram (HTTP {http_status})."
    )
    codigo = err.get("code")
    subcodigo = err.get("error_subcode")

    if subcodigo == ERRO_JA_RESPONDIDO:
        return InstagramApiError(
            "Este comentário já recebeu a resposta privada permitida pela Meta.",
            codigo=codigo, subcodigo=subcodigo, http_status=http_status, permanente=True,
        )
    if codigo in _CODIGOS_TRANSITORIOS or http_status >= 500:
        return InstagramApiError(
            msg, codigo=codigo, subcodigo=subcodigo, http_status=http_status, permanente=False
        )
    if codigo in _CODIGOS_PERMANENTES or 400 <= http_status < 500:
        return InstagramApiError(
            msg, codigo=codigo, subcodigo=subcodigo, http_status=http_status, permanente=True
        )
    return InstagramApiError(
        msg, codigo=codigo, subcodigo=subcodigo, http_status=http_status, permanente=False
    )


async def _request(
    method: str,
    url: str,
    *,
    params: Optional[dict] = None,
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
    data: Optional[dict] = None,
) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(
                method, url, params=params, json=json_body, data=data, headers=headers
            )
    except httpx.TimeoutException as exc:
        raise InstagramApiError(f"Timeout na API do Instagram: {exc}", permanente=False)
    except httpx.RequestError as exc:
        raise InstagramApiError(f"Erro de conexão com o Instagram: {exc}", permanente=False)

    try:
        body = response.json()
    except Exception:
        body = {}

    if response.status_code >= 400 or (isinstance(body, dict) and body.get("error")):
        erro = _classificar_erro(body if isinstance(body, dict) else {}, response.status_code)
        logger.warning(
            "Instagram API %s %s -> %s (codigo=%s subcodigo=%s permanente=%s): %s",
            method, url.split("?")[0], response.status_code,
            erro.codigo, erro.subcodigo, erro.permanente, erro.mensagem,
        )
        raise erro

    return body if isinstance(body, dict) else {"data": body}


# --------------------------------------------------------------------------- #
#  OAuth                                                                       #
# --------------------------------------------------------------------------- #


def build_authorize_url(redirect_uri: str, state: str) -> str:
    """Diálogo do Business Login for Instagram."""
    app_id, _ = _require_app_credentials()
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(DEFAULT_SCOPES),
        "state": state,
        # Documentado: "When set to true it forces an app user to use their Instagram
        # professional account credentials to log into your app EVEN IF the user is
        # logged into Instagram". Sem isso, uma aluna logada no navegador com o
        # perfil pessoal (ou com o perfil de outra pessoa) conectaria a conta errada
        # sem ver tela de login nenhuma — e só descobriria quando os directs saíssem
        # do perfil errado.
        "force_reauth": "true",
    }
    return f"{OAUTH_AUTHORIZE_BASE}?{urlencode(params)}"


async def exchange_code_for_short_token(code: str, redirect_uri: str) -> dict[str, Any]:
    """code → token de curta duração (1h). POST form-urlencoded, não JSON.

    A Meta às vezes acrescenta `#_` no fim do code quando o retorno passa pelo
    navegador; enviar isso invalida a troca.
    """
    app_id, app_secret = _require_app_credentials()
    limpo = (code or "").split("#")[0]
    data = {
        "client_id": app_id,
        "client_secret": app_secret,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
        "code": limpo,
    }
    return await _request("POST", OAUTH_TOKEN_URL, data=data)


def permissoes_concedidas(resposta_token: dict) -> list[str]:
    """Escopos que a aluna REALMENTE concedeu, lidos da resposta do OAuth.

    Guardar o que pedimos em vez do que foi concedido esconde o pior cenário: a
    aluna desmarca uma permissão na tela de consentimento, tudo conecta, e só a
    resposta pública para de funcionar — sem erro em lugar nenhum.

    A Meta devolve `permissions` ora como lista, ora como string separada por
    vírgula. Ausente (formato mudou), devolvemos [] e quem chama decide.
    """
    bruto = (resposta_token or {}).get("permissions")
    if isinstance(bruto, list):
        return [str(p).strip() for p in bruto if str(p).strip()]
    if isinstance(bruto, str):
        return [p.strip() for p in bruto.split(",") if p.strip()]
    return []


async def exchange_for_long_lived_token(short_token: str) -> dict[str, Any]:
    """Token curto → token longo (60 dias)."""
    _, app_secret = _require_app_credentials()
    params = {
        "grant_type": "ig_exchange_token",
        "client_secret": app_secret,
        "access_token": short_token,
    }
    return await _request("GET", f"{GRAPH_BASE}/access_token", params=params)


async def refresh_long_lived_token(long_token: str) -> dict[str, Any]:
    """Renova por mais 60 dias. Só funciona com token AINDA válido.

    Token vencido não renova — a aluna precisa refazer o login. É por isso que o
    cron roda diariamente com folga de 10 dias.
    """
    params = {"grant_type": "ig_refresh_token", "access_token": long_token}
    return await _request("GET", f"{GRAPH_BASE}/refresh_access_token", params=params)


async def get_me(access_token: str) -> dict[str, Any]:
    """Perfil do dono do token, incluindo `account_type` (para barrar conta pessoal)."""
    params = {
        "fields": "user_id,username,name,account_type,profile_picture_url",
        "access_token": access_token,
    }
    return await _request("GET", _graph_url("me"), params=params)


# --------------------------------------------------------------------------- #
#  Mídias                                                                      #
# --------------------------------------------------------------------------- #


async def list_media(access_token: str, limit: int = 24, after: Optional[str] = None) -> dict[str, Any]:
    """Publicações do perfil, mais recentes primeiro, paginadas por cursor.

    Retorna o envelope cru (`data` + `paging`) porque a tela usa "Carregar mais".
    """
    params = {
        "fields": (
            "id,caption,media_type,media_product_type,permalink,"
            "media_url,thumbnail_url,timestamp"
        ),
        "limit": limit,
        "access_token": access_token,
    }
    if after:
        params["after"] = after
    return await _request("GET", _graph_url("me/media"), params=params)


async def list_stories(access_token: str) -> dict[str, Any]:
    """Stories ATIVOS (últimas 24h) da conta, para o seletor da automação.

    O edge /stories não pagina como o /media (um dia tem poucas dezenas no
    máximo) e não devolve caption pesquisável — devolvemos o envelope cru.
    Stories de Live não vêm; reshares também não (limitação documentada).
    """
    params = {
        "fields": (
            "id,caption,media_type,media_product_type,media_url,thumbnail_url,timestamp"
        ),
        "access_token": access_token,
    }
    return await _request("GET", _graph_url("me/stories"), params=params)


async def get_media(access_token: str, media_id: str) -> dict[str, Any]:
    params = {
        "fields": "id,caption,media_type,media_product_type,permalink,thumbnail_url,media_url,timestamp",
        "access_token": access_token,
    }
    return await _request("GET", _graph_url(media_id), params=params)


# --------------------------------------------------------------------------- #
#  Envio                                                                       #
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
#  Assinatura de webhook por CONTA                                             #
# --------------------------------------------------------------------------- #

# Assinar o campo no painel é o passo 2 de 4 e vale só para o APP. O passo 3 —
# este — habilita a CONTA da aluna a receber notificação. Sem ele o webhook nunca
# dispara e NÃO há erro em lugar nenhum: o OAuth funciona, a tela funciona, a
# automação salva, e nada acontece.
#
# Só `comments`: é o único campo que a feature usa, e ele exige apenas
# `instagram_business_basic` + `instagram_business_manage_comments`. Incluir
# `messages` aqui arrastaria um requisito de permissão que o v1 não precisa.
# `messages` cobre TODAS as DMs da conta (a Meta não filtra por tipo) — o
# webhook descarta o que não é reply de story. Necessário para a automação de
# story: o reply chega como mensagem com reply_to.story.
CAMPOS_WEBHOOK = ["comments", "messages"]


async def subscribe_account_to_webhooks(access_token: str, ig_user_id: str) -> bool:
    """Inscreve a conta da aluna nas notificações de comentário.

    Idempotente: reinscrever uma conta já inscrita devolve `success: true` de novo.

    O token vai como parâmetro `access_token` (é a forma que a doc usa em todos os
    exemplos deste endpoint), não como header Bearer.
    """
    resposta = await _request(
        "POST",
        _graph_url(f"{ig_user_id}/subscribed_apps"),
        params={
            "subscribed_fields": ",".join(CAMPOS_WEBHOOK),
            "access_token": access_token,
        },
    )
    return bool(resposta.get("success", True))


async def get_account_subscriptions(access_token: str, ig_user_id: str) -> Optional[list]:
    """Lê as inscrições da conta, para conferência.

    O GET deste edge NÃO é documentado no caminho Instagram Login (só no nó Page,
    em outro host). Se a Meta recusar, devolvemos None — "não deu para verificar"
    é diferente de "não está inscrito", e tratar os dois igual faria a tela
    alarmar uma conta que está perfeitamente configurada.
    """
    try:
        corpo = await _request(
            "GET",
            _graph_url(f"{ig_user_id}/subscribed_apps"),
            params={"access_token": access_token},
        )
    except InstagramApiError as exc:
        logger.info(
            "Instagram: GET subscribed_apps indisponível para %s (%s)", ig_user_id, exc.mensagem
        )
        return None
    return corpo.get("data")


def montar_mensagem_dm(
    texto: str, link: Optional[str] = None, botao_texto: Optional[str] = None
) -> dict[str, Any]:
    """Monta o `message` do direct: template com botão, ou texto puro.

    Com link E texto de botão, vai como template `button` da Meta — texto em
    cima, um `web_url` embaixo. Link colado no meio da mensagem parece spam;
    botão parece mensagem de marca.

    Sem os dois, cai no formato antigo (texto puro). Esse caminho é o
    **fallback**: se a Meta recusar o template em produção, é só o envio parar
    de mandar link/botão que tudo volta a funcionar, sem mexer no resto.
    """
    if link and botao_texto:
        return {
            "attachment": {
                "type": "template",
                "payload": {
                    "template_type": "button",
                    "text": texto,
                    "buttons": [
                        {"type": "web_url", "url": link, "title": botao_texto[:20]}
                    ],
                },
            }
        }
    # Formato antigo: link (quando existe) colado no fim do texto.
    corpo = f"{texto}\n\n{link}" if link else texto
    return {"text": corpo}


async def send_private_reply(
    access_token: str,
    ig_user_id: str,
    comment_id: str,
    texto: str,
    link: Optional[str] = None,
    botao_texto: Optional[str] = None,
) -> dict[str, Any]:
    """Private reply: a mensagem no direct disparada por um comentário.

    UMA por comentário, para sempre (subcode 2534014 na segunda tentativa), e só
    dentro de 7 dias a partir da criação do comentário.

    Entrega: Caixa de Entrada se a pessoa segue o perfil; pasta Solicitações se
    não segue. Isso não é controlável pela API — está avisado na tela.
    """
    return await _request(
        "POST",
        _graph_url(f"{ig_user_id}/messages"),
        json_body={
            "recipient": {"comment_id": str(comment_id)},
            "message": montar_mensagem_dm(texto, link, botao_texto),
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )


async def send_story_reply_dm(
    access_token: str,
    ig_user_id: str,
    recipient_id: str,
    texto: str,
    link: Optional[str] = None,
    botao_texto: Optional[str] = None,
) -> dict[str, Any]:
    """DM em resposta a um reply de story — Send API com recipient por IGSID.

    Diferente do private reply (recipient por comment_id), aqui a pessoa JÁ nos
    mandou uma mensagem (o reply do story), então vale a janela padrão de 24h
    da mensageria — respondemos a conversa que ela abriu.
    """
    return await _request(
        "POST",
        _graph_url(f"{ig_user_id}/messages"),
        json_body={
            "recipient": {"id": str(recipient_id)},
            "message": montar_mensagem_dm(texto, link, botao_texto),
        },
        headers={"Authorization": f"Bearer {access_token}"},
    )


async def reply_to_comment(access_token: str, comment_id: str, texto: str) -> dict[str, Any]:
    """Resposta pública embaixo do comentário."""
    return await _request(
        "POST",
        _graph_url(f"{comment_id}/replies"),
        data={"message": texto},
        headers={"Authorization": f"Bearer {access_token}"},
    )
