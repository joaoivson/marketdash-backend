"""Webhook do Instagram: notificações de comentário e callbacks obrigatórios da Meta.

Montado FORA do /api/v1 (em /webhooks) porque a URL fica cadastrada no painel da
Meta e não deve carregar versionamento de API interna.

Três endpoints exigidos pela Meta:
  GET  /webhooks/instagram              handshake (hub.challenge)
  POST /webhooks/instagram              notificações do campo `comments`
  POST /webhooks/instagram/deauthorize  a aluna removeu o app pelo Instagram
  POST /webhooks/instagram/data-deletion  pedido de exclusão de dados
"""

import base64
import hashlib
import hmac
import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request, Response, status

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["instagram-webhook"])


# --------------------------------------------------------------------------- #
#  Assinatura                                                                  #
# --------------------------------------------------------------------------- #


def verificar_assinatura(corpo: bytes, header_assinatura: Optional[str]) -> bool:
    """Confere o X-Hub-Signature-256 (HMAC-SHA256 do corpo CRU com o app secret).

    Precisa ser o corpo CRU: reserializar o JSON muda espaços e ordem de chaves e
    quebra o HMAC. Comparação com `compare_digest` para não vazar informação por
    tempo de resposta.
    """
    if not settings.INSTAGRAM_APP_SECRET:
        logger.error("INSTAGRAM_APP_SECRET ausente — recusando webhook do Instagram")
        return False
    if not header_assinatura or not header_assinatura.startswith("sha256="):
        return False
    esperado = hmac.new(
        settings.INSTAGRAM_APP_SECRET.encode("utf-8"), corpo, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, header_assinatura.split("=", 1)[1].strip())


def _parse_signed_request(signed_request: str) -> Optional[dict[str, Any]]:
    """Decodifica o `signed_request` dos callbacks de deauth / exclusão de dados.

    Formato da Meta: `<assinatura_b64url>.<payload_b64url>`. A assinatura é
    HMAC-SHA256 do payload codificado, com o app secret.
    """
    if not signed_request or "." not in signed_request:
        return None
    if not settings.INSTAGRAM_APP_SECRET:
        logger.error("INSTAGRAM_APP_SECRET ausente — recusando signed_request")
        return None

    assinatura_b64, payload_b64 = signed_request.split(".", 1)

    def _b64(valor: str) -> bytes:
        # base64url sem padding, como a Meta manda.
        return base64.urlsafe_b64decode(valor + "=" * (-len(valor) % 4))

    try:
        assinatura = _b64(assinatura_b64)
        payload = json.loads(_b64(payload_b64))
    except Exception as exc:
        logger.warning("signed_request do Instagram ilegível: %s", exc)
        return None

    esperado = hmac.new(
        settings.INSTAGRAM_APP_SECRET.encode("utf-8"),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    if not hmac.compare_digest(esperado, assinatura):
        logger.warning("signed_request do Instagram com assinatura inválida")
        return None
    return payload


# --------------------------------------------------------------------------- #
#  Handshake                                                                   #
# --------------------------------------------------------------------------- #


@router.get("/instagram", include_in_schema=False)
def verificar_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
):
    """Handshake da Meta ao cadastrar a URL do webhook.

    Responde o `hub.challenge` em TEXTO PURO — a Meta rejeita se vier como JSON.
    """
    if not settings.INSTAGRAM_WEBHOOK_VERIFY_TOKEN:
        logger.error("INSTAGRAM_WEBHOOK_VERIFY_TOKEN ausente — handshake recusado")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Não configurado")
    if hub_mode != "subscribe" or not hmac.compare_digest(
        hub_verify_token or "", settings.INSTAGRAM_WEBHOOK_VERIFY_TOKEN
    ):
        logger.warning("Handshake do webhook do Instagram com token inválido")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Token inválido")
    return Response(content=hub_challenge or "", media_type="text/plain")


# --------------------------------------------------------------------------- #
#  Notificações de comentário                                                  #
# --------------------------------------------------------------------------- #


@router.post("/instagram", include_in_schema=False)
async def receber_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
):
    """Recebe as notificações do campo `comments`.

    Responde 200 IMEDIATAMENTE e processa em fila. A Meta considera o webhook
    falho se demorar, e passa a reentregar (ou a desativar a assinatura) — nunca
    processar dentro do request.
    """
    corpo = await request.body()

    if not verificar_assinatura(corpo, x_hub_signature_256):
        logger.warning(
            "Webhook do Instagram com assinatura inválida (ip=%s)",
            request.client.host if request.client else "?",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Assinatura inválida")

    try:
        payload = json.loads(corpo or b"{}")
    except Exception:
        logger.warning("Webhook do Instagram com corpo não-JSON")
        return {"status": "ignored"}

    if payload.get("object") != "instagram":
        return {"status": "ignored"}

    enfileirados = 0
    for entrada in payload.get("entry") or []:
        ig_user_id = str(entrada.get("id") or "")
        if not ig_user_id:
            continue

        # Replies de STORY chegam pelo campo `messages` (story não tem
        # comentário): cada item de entry.messaging é uma DM. Assinar `messages`
        # faz TODAS as DMs da conta baterem aqui — o descarte do que não é reply
        # de story acontece NESTE primeiro filtro, e nada além do necessário
        # segue adiante (mesma política do monitoramento de grupos).
        for msg_evt in entrada.get("messaging") or []:
            mensagem = msg_evt.get("message") or {}
            if mensagem.get("is_echo"):
                continue  # eco do que NÓS enviamos — responder seria loop
            story = (mensagem.get("reply_to") or {}).get("story") or {}
            if not story.get("id"):
                continue  # DM comum/reação — não é reply de story, descarta
            remetente = str(((msg_evt.get("sender") or {}).get("id")) or "")
            if not remetente or remetente == ig_user_id:
                continue
            _enfileirar_story_reply(
                ig_user_id,
                {
                    "mid": mensagem.get("mid"),
                    "sender_id": remetente,
                    "story_id": str(story.get("id")),
                    "text": mensagem.get("text") or "",
                    # epoch em MILISSEGUNDOS na mensageria (o parse normaliza)
                    "timestamp": msg_evt.get("timestamp"),
                },
            )
            enfileirados += 1

        for mudanca in entrada.get("changes") or []:
            if mudanca.get("field") != "comments":
                continue
            valor = mudanca.get("value") or {}
            # O webhook nem sempre traz `timestamp` no comentário; `entry.time`
            # (epoch) é a melhor aproximação disponível para a janela de 7 dias.
            if not valor.get("timestamp") and entrada.get("time"):
                valor = {**valor, "timestamp": entrada.get("time")}
            _enfileirar(ig_user_id, valor)
            enfileirados += 1

    return {"status": "ok", "enfileirados": enfileirados}


def _enfileirar(ig_user_id: str, valor: dict) -> None:
    """Manda para o Celery. Broker fora do ar não pode derrubar o webhook.

    Se a fila estiver indisponível, respondemos 200 assim mesmo: um 5xx faria a
    Meta reentregar (bom) mas, em pico, também derrubaria a assinatura do
    webhook (ruim). O comentário perdido fica registrado no log de erro.
    """
    try:
        from app.tasks.instagram_tasks import processar_comentario_instagram_task

        processar_comentario_instagram_task.apply_async(
            kwargs={"ig_user_id": ig_user_id, "valor": valor}, priority=0
        )
    except Exception as exc:
        logger.error(
            "Instagram: falha ao enfileirar comentário ig_user_id=%s comment=%s: %s",
            ig_user_id, (valor or {}).get("id"), exc,
        )


def _enfileirar_story_reply(ig_user_id: str, evento: dict) -> None:
    """Mesma política do _enfileirar: broker fora do ar não derruba o webhook."""
    try:
        from app.tasks.instagram_tasks import processar_story_reply_instagram_task

        processar_story_reply_instagram_task.apply_async(
            kwargs={"ig_user_id": ig_user_id, "evento": evento}, priority=0
        )
    except Exception as exc:
        logger.error(
            "Instagram: falha ao enfileirar reply de story ig_user_id=%s mid=%s: %s",
            ig_user_id, (evento or {}).get("mid"), exc,
        )


# --------------------------------------------------------------------------- #
#  Callbacks obrigatórios                                                      #
# --------------------------------------------------------------------------- #


@router.post("/instagram/deauthorize", include_in_schema=False)
async def deauthorize(request: Request):
    """A aluna removeu o MarketDash em Instagram → Apps e sites.

    Marca a conexão como revogada e pausa as automações. Nada é apagado: se ela
    reconectar, reencontra tudo como deixou.
    """
    payload = await _payload_signed_request(request)
    ig_user_id = str((payload or {}).get("user_id") or "")
    if not ig_user_id:
        return {"status": "ignored"}

    from app.db.session import SessionLocal
    from app.repositories.instagram_automation_repository import InstagramAutomationRepository
    from app.services.instagram_connection_service import InstagramConnectionService

    db = SessionLocal()
    try:
        InstagramConnectionService(InstagramAutomationRepository(db)).handle_deauthorize(ig_user_id)
    finally:
        db.close()
    return {"status": "ok"}


@router.post("/instagram/data-deletion", include_in_schema=False)
async def data_deletion(request: Request):
    """Pedido de exclusão de dados. Apaga conexão, automações e eventos.

    A Meta exige a resposta no formato {url, confirmation_code} — a URL é onde a
    pessoa acompanha o pedido.
    """
    payload = await _payload_signed_request(request)
    ig_user_id = str((payload or {}).get("user_id") or "")

    from app.db.session import SessionLocal
    from app.repositories.instagram_automation_repository import InstagramAutomationRepository
    from app.services.instagram_connection_service import InstagramConnectionService

    codigo = f"ig-{ig_user_id or 'desconhecido'}"
    if ig_user_id:
        db = SessionLocal()
        try:
            InstagramConnectionService(InstagramAutomationRepository(db)).handle_data_deletion(ig_user_id)
        finally:
            db.close()

    base = (settings.INSTAGRAM_OAUTH_REDIRECT_URI or "").split("/dashboard")[0] or ""
    return {
        "url": f"{base}/exclusao-de-dados?code={codigo}",
        "confirmation_code": codigo,
    }


async def _payload_signed_request(request: Request) -> Optional[dict[str, Any]]:
    """Extrai o signed_request, que a Meta manda como form-urlencoded."""
    try:
        form = await request.form()
        assinado = form.get("signed_request")
    except Exception:
        assinado = None
    if not assinado:
        # Alguns ambientes de teste mandam JSON.
        try:
            corpo = json.loads(await request.body() or b"{}")
            assinado = corpo.get("signed_request")
        except Exception:
            assinado = None
    if not assinado:
        logger.warning("Callback do Instagram sem signed_request")
        return None
    return _parse_signed_request(str(assinado))
