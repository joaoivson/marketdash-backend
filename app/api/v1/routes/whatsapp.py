"""
Resumo diário no WhatsApp: opt-in da afiliada, webhook do SAIR e estado do número.

O webhook é a única rota aqui sem sessão de usuária — quem chama é a Evolution.
Ele se autentica por token no header.
"""
import hmac
import logging
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_admin, require_plan
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.repositories.whatsapp_repository import WhatsappRepository
from app.schemas.whatsapp import (
    ConfirmarRequest, InstanciaResponse, OptinRequest, StatusResponse,
)
from app.services.evolution_client import ErroWhatsapp, EvolutionClient
from app.services.whatsapp_optin_service import (
    CodigoInvalido, TentativasEsgotadas, WhatsappIndisponivel,
    WhatsappOptinService, pediu_para_sair,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


def _cliente() -> EvolutionClient:
    return EvolutionClient(
        settings.EVOLUTION_URL, settings.EVOLUTION_API_KEY, settings.EVOLUTION_INSTANCIA
    )


def _servico(db: Session) -> WhatsappOptinService:
    return WhatsappOptinService(WhatsappRepository(db), _cliente())


def url_do_webhook(request: Request) -> str:
    """
    URL pública do webhook, do jeito que a Evolution precisa alcançar.

    `request.url_for` monta a partir do esquema que chega no container — http,
    porque quem termina o TLS é o proxy. A Evolution recebia
    `http://api.hml...`, que responde 301, e ela não segue redirecionamento:
    o SAIR nunca chegava e a afiliada continuava recebendo. X-Forwarded-Proto é
    o que o proxy diz sobre a requisição original.
    """
    url = str(request.url_for("webhook"))
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto in ("http", "https"):
        url = re.sub(r"^https?://", f"{proto}://", url)
    return url


INDISPONIVEL = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="O envio por WhatsApp está indisponível no momento.",
)


@router.get("/status", response_model=StatusResponse)
def status_do_optin(
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    return StatusResponse(**_servico(db).status(current_user.id))


@router.post("/optin", response_model=StatusResponse)
def registrar(
    payload: OptinRequest,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    try:
        servico.registrar(current_user.id, payload.numero)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except WhatsappIndisponivel:
        raise INDISPONIVEL
    except ErroWhatsapp as e:
        if e.motivo == "numero_invalido":
            raise HTTPException(
                status_code=400,
                detail="Não encontramos esse número no WhatsApp. Confira e tente de novo.",
            )
        logger.warning("Opt-in falhou para user %s (%s)", current_user.id, e.motivo)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não conseguimos enviar o código agora. Tente de novo em alguns minutos.",
        )
    return StatusResponse(**servico.status(current_user.id))


@router.post("/confirmar", response_model=StatusResponse)
def confirmar(
    payload: ConfirmarRequest,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    try:
        servico.confirmar(current_user.id, payload.codigo)
    except TentativasEsgotadas:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas. Cadastre o número de novo para receber outro código.",
        )
    except CodigoInvalido:
        raise HTTPException(status_code=400, detail="Código inválido ou expirado.")
    return StatusResponse(**servico.status(current_user.id))


@router.delete("/optin", response_model=StatusResponse)
def desligar(
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    servico.desligar(current_user.id)
    return StatusResponse(**servico.status(current_user.id))


@router.get("/instancia", response_model=InstanciaResponse)
def instancia(request: Request, _: User = Depends(require_admin)):
    """
    Estado do número do MarketDash + QR quando precisa parear. Só admin.

    Se a instância ainda não existe na Evolution, cria e já aponta o webhook do
    SAIR. Antes isso eram dois `curl` no passo a passo de implantação — passos
    que precisam ser refeitos toda vez que o ambiente é recriado ou o número é
    trocado depois de um banimento, e que ninguém lembra na hora certa.
    """
    cliente = _cliente()
    if not cliente.configurado():
        return InstanciaResponse(configurado=False, estado="sem_config")

    try:
        if not cliente.instancia_existe():
            logger.info("Instância %s não existe na Evolution — criando",
                        settings.EVOLUTION_INSTANCIA)
            cliente.criar_instancia()

        # Reconcilia a cada abertura da tela, não só na criação: um webhook
        # apontando para o lugar errado falha em SILÊNCIO — a afiliada responde
        # SAIR, nada acontece, e a próxima notícia é uma denúncia. Já aconteceu
        # aqui, com http:// no lugar de https://.
        if settings.EVOLUTION_WEBHOOK_TOKEN:
            desejada = url_do_webhook(request)
            atual = cliente.webhook_atual()
            if (atual.get("url") != desejada or not atual.get("enabled")
                    or atual.get("events") != ["MESSAGES_UPSERT"]):
                cliente.configurar_webhook(desejada, settings.EVOLUTION_WEBHOOK_TOKEN)
                logger.info("Webhook do WhatsApp reapontado: %s → %s",
                            atual.get("url"), desejada)
        else:
            # Sem webhook, quem responder SAIR continua recebendo — e é
            # assim que um número é denunciado.
            logger.error("EVOLUTION_WEBHOOK_TOKEN ausente: o SAIR não vai funcionar")
    except ErroWhatsapp as e:
        return InstanciaResponse(configurado=True, estado=f"erro: {e.motivo}")

    try:
        estado = cliente.estado()
    except ErroWhatsapp as e:
        return InstanciaResponse(configurado=True, estado=f"erro: {e.motivo}")

    qr = None
    if estado != "open":
        try:
            dados = cliente.qrcode()
            qr = dados.get("base64") or (dados.get("qrcode") or {}).get("base64")
        except ErroWhatsapp:
            qr = None
    return InstanciaResponse(configurado=True, estado=estado, qrcode=qr)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
):
    """
    Mensagens recebidas no número do MarketDash.

    Só existe para o SAIR. Devolve 200 em qualquer caso: erro aqui faz a
    Evolution reenfileirar o evento, e ficar reprocessando a mesma mensagem não
    ajuda ninguém.
    """
    esperado = settings.EVOLUTION_WEBHOOK_TOKEN
    if not esperado or not x_webhook_token or not hmac.compare_digest(x_webhook_token, esperado):
        # 200 de propósito: um 401 diria a quem sonda que existe token certo.
        logger.warning("Webhook do WhatsApp com token inválido")
        return {"ok": True}

    try:
        evento = await request.json()
    except Exception:
        return {"ok": True}

    dados = (evento or {}).get("data") or {}
    chave = dados.get("key") or {}
    if chave.get("fromMe"):
        return {"ok": True}

    remetente = str(chave.get("remoteJid") or "")
    numero = remetente.split("@")[0].split(":")[0]
    mensagem = dados.get("message") or {}
    texto = (
        mensagem.get("conversation")
        or (mensagem.get("extendedTextMessage") or {}).get("text")
        or ""
    )

    if numero and pediu_para_sair(texto):
        servico = _servico(db)
        n = servico.desligar_por_numero(numero)
        if n:
            logger.info("SAIR pelo WhatsApp desligou %s opt-in(s)", n)
            try:
                _cliente().enviar_texto(
                    numero,
                    "Pronto, você não vai mais receber o resumo diário. "
                    "Se mudar de ideia, é só religar nas Configurações do MarketDash.",
                )
            except ErroWhatsapp:
                pass   # já desligamos; a confirmação é cortesia
    return {"ok": True}
