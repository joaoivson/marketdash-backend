"""
Resumo diário no WhatsApp (opt-in, estado da sessão do MarketDash) e o
webhook ÚNICO de eventos do WAHA — multi-sessão, roteado pelo nome.

O webhook é a única rota aqui sem sessão de usuária — quem chama é o WAHA.
Ele se autentica por token no header e devolve 200 em qualquer caso: erro
aqui faz o WAHA re-tentar, e reprocessar o mesmo evento não ajuda ninguém.
"""
import hmac
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_admin, require_plan
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository
from app.repositories.whatsapp_repository import WhatsappRepository
from app.schemas.whatsapp import (
    ConfirmarRequest, InstanciaResponse, OptinRequest, StatusResponse,
)
from app.services.waha_client import (
    ErroWhatsapp, WahaClient, chat_id_de_numero, numero_de_jid,
    campo,
)
from app.services.whatsapp_instancia_service import (
    aplicar_evento_de_status, config_de_webhook, pertence_a_este_ambiente,
)
from app.services.whatsapp_optin_service import (
    CodigoInvalido, TentativasEsgotadas, WhatsappIndisponivel,
    WhatsappOptinService, pediu_para_sair,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])

# A sessão do resumo precisa do `message` (é o SAIR); as das alunas, não.
EVENTOS_DA_SESSAO_RESUMO = ["message", "session.status"]


def _cliente_resumo() -> WahaClient:
    return WahaClient(
        settings.WAHA_URL, settings.WAHA_API_KEY, settings.WAHA_SESSAO_RESUMO
    )


def _servico(db: Session) -> WhatsappOptinService:
    return WhatsappOptinService(WhatsappRepository(db), _cliente_resumo())


def url_do_webhook(request: Request) -> str:
    """
    URL pública do webhook. A configurada (WAHA_WEBHOOK_URL) manda; o fallback
    deriva da request corrigindo o esquema via X-Forwarded-Proto — `url_for`
    cru atrás do proxy gera http://, toma 301 e falha em silêncio (já mordeu).
    """
    if settings.WAHA_WEBHOOK_URL:
        return settings.WAHA_WEBHOOK_URL
    import re
    url = str(request.url_for("webhook"))
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto in ("http", "https"):
        url = re.sub(r"^https?://", f"{proto}://", url)
    return url


def _webhook_divergente(info: dict, desejado: dict) -> bool:
    atuais = ((info.get("config") or {}).get("webhooks")) or []
    if not atuais:
        return True
    atual = atuais[0] or {}
    return (
        atual.get("url") != desejado.get("url")
        or sorted(atual.get("events") or []) != sorted(desejado.get("events") or [])
    )


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
    Estado da sessão do RESUMO (o número do MarketDash) + QR para parear.
    Só admin. Cria a sessão no WAHA se ainda não existe e reconcilia o webhook
    a cada abertura da tela — webhook errado falha em SILÊNCIO (o SAIR não
    chega) e a próxima notícia é uma denúncia.
    """
    cliente = _cliente_resumo()
    if not cliente.configurado():
        return InstanciaResponse(configurado=False, estado="sem_config")
    if not settings.WAHA_WEBHOOK_TOKEN:
        logger.error("WAHA_WEBHOOK_TOKEN ausente: o SAIR não vai funcionar")

    webhooks = None
    if settings.WAHA_WEBHOOK_TOKEN:
        webhooks = config_de_webhook(url_do_webhook(request), EVENTOS_DA_SESSAO_RESUMO)

    try:
        info = cliente.sessao_info()
        if not info:
            logger.info("Sessão %s não existe no WAHA — criando",
                        settings.WAHA_SESSAO_RESUMO)
            cliente.criar_sessao(webhooks=webhooks)
            info = cliente.sessao_info()
        elif webhooks and _webhook_divergente(info, webhooks[0]):
            # Só reescreve quando a config REALMENTE mudou: o PUT reinicia a
            # sessão, e reiniciar a cada abertura da tela derruba um lote de
            # resumo em andamento (envios passam a falhar como "desconectado").
            logger.info("Webhook do resumo reapontado")
            cliente.atualizar_sessao(webhooks)
        estado = str(info.get("status") or "inexistente")
    except ErroWhatsapp as e:
        return InstanciaResponse(configurado=True, estado=f"erro: {e.motivo}")

    if estado == "WORKING":
        # A tela do admin espera "open" desde a era Evolution — manter o
        # contrato poupa o frontend de conhecer os estados do WAHA.
        return InstanciaResponse(configurado=True, estado="open")

    qr = None
    try:
        qr = cliente.qrcode()
    except ErroWhatsapp:
        qr = None
    return InstanciaResponse(configurado=True, estado=estado.lower(), qrcode=qr)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
):
    """
    Eventos do WAHA — TODAS as sessões (resumo + números das afiliadas).

    Roteamento pelo nome da sessão: só tratamos sessões com o prefixo DESTE
    ambiente (mkd{ref4}) ou a sessão do resumo. Evento de sessão alheia
    (outro ambiente no mesmo servidor WAHA) é ignorado em silêncio — tratar
    seria fratricídio hml×prod.
    """
    esperado = settings.WAHA_WEBHOOK_TOKEN
    if not esperado or not x_webhook_token or not hmac.compare_digest(x_webhook_token, esperado):
        # 200 de propósito: um 401 diria a quem sonda que existe token certo.
        logger.warning("Webhook do WhatsApp com token inválido")
        return {"ok": True}

    try:
        evento = await request.json()
    except Exception:
        return {"ok": True}

    nome_sessao = str((evento or {}).get("session") or "")
    tipo = str((evento or {}).get("event") or "")
    payload = (evento or {}).get("payload") or {}

    if tipo == "session.status":
        _tratar_status(db, nome_sessao, evento, payload)
    elif tipo == "message":
        _tratar_mensagem(db, nome_sessao, payload)
    elif tipo in ("group.v2.participants", "group.participants"):
        _tratar_participantes(db, nome_sessao, payload)
    return {"ok": True}


def _tratar_status(db: Session, nome_sessao: str, evento: dict, payload: dict) -> None:
    status_waha = str(payload.get("status") or "")
    if nome_sessao == settings.WAHA_SESSAO_RESUMO:
        logger.info("Sessão do resumo: %s", status_waha)
        return
    if not pertence_a_este_ambiente(nome_sessao):
        return  # sessão de outro ambiente — não é nossa
    repo = WhatsappInstanciaRepository(db)
    instancia = repo.por_nome(nome_sessao)
    if not instancia:
        logger.warning("session.status de sessão desconhecida: %s", nome_sessao)
        return
    me = (evento or {}).get("me") or {}
    numero = numero_de_jid(me.get("id")) or None
    aplicar_evento_de_status(repo, instancia, status_waha, numero)


def _tratar_participantes(db: Session, nome_sessao: str, payload: dict) -> None:
    """
    Entradas e saídas de grupo (F6). Só sessões DESTE ambiente, e só de quem
    conhecemos — evento de sessão alheia é descartado em silêncio.

    O JID do participante vira hash no service, antes de qualquer persistência.
    """
    if not pertence_a_este_ambiente(nome_sessao):
        return
    repo = WhatsappInstanciaRepository(db)
    instancia = repo.por_nome(nome_sessao)
    if not instancia:
        return

    # Leitura tolerante à caixa: o schema documentado deste evento é camelCase,
    # mas a doc do REST /groups também era — e o GOWS devolvia PascalCase, o que
    # zerou o sync em 26/08 sem erro nenhum. Aqui o preço de errar é o mesmo:
    # nenhuma entrada/saída registrada, em silêncio, e F6 inteira sem dado.
    grupo_jid = str(campo(campo(payload, "group") or {}, "id", "JID") or "")
    acao = str(campo(payload, "type", "action") or "")
    participantes = [
        str(campo(p, "id", "JID", "PhoneNumber", "LID") or "")
        for p in (campo(payload, "participants") or [])
    ]
    participantes = [p for p in participantes if p]
    if not grupo_jid or not participantes:
        return

    from app.services.grupo_evento_service import GrupoEventoService

    try:
        GrupoEventoService(db).registrar(instancia.user_id, grupo_jid, acao,
                                         participantes)
    except Exception:
        db.rollback()
        # Webhook nunca propaga exceção: o WAHA reenviaria o evento em loop.
        logger.exception("Falha ao registrar evento de participantes")


def _tratar_mensagem(db: Session, nome_sessao: str, payload: dict) -> None:
    """
    `message` chega em dois casos, e só nesses dois:

      * sessão do resumo → palavra SAIR (opt-out);
      * sessão de aluna COM monitoramento ativo (F8) → captura de oferta.

    Sessão de aluna sem monitoramento sequer assina o evento, então o conteúdo
    dos grupos dela não chega aqui.
    """
    if payload.get("fromMe"):
        return
    if nome_sessao != settings.WAHA_SESSAO_RESUMO:
        _tratar_mensagem_de_monitoramento(db, nome_sessao, payload)
        return
    remetente = str(payload.get("from") or "")
    if remetente.endswith("@g.us"):
        # Mensagem de grupo no número do resumo: SAIR ali desligaria o resumo
        # de um número que por acaso está no grupo. Ignorar é a única resposta.
        return
    numero = numero_de_jid(remetente)
    texto = str(payload.get("body") or "")

    if numero and pediu_para_sair(texto):
        servico = _servico(db)
        n = servico.desligar_por_numero(numero)
        if n:
            logger.info("SAIR pelo WhatsApp desligou %s opt-in(s)", n)
            try:
                _cliente_resumo().enviar_texto(
                    chat_id_de_numero(numero),
                    "Pronto, você não vai mais receber o resumo diário. "
                    "Se mudar de ideia, é só religar nas Configurações do MarketDash.",
                )
            except ErroWhatsapp:
                pass   # já desligamos; a confirmação é cortesia


def _tratar_mensagem_de_monitoramento(db: Session, nome_sessao: str,
                                      payload: dict) -> None:
    """
    Mensagem de grupo numa sessão de aluna com monitoramento ativo.

    O filtro roda ANTES de persistir: o que não é oferta é descartado na
    memória. Gravar-para-depois-filtrar tornaria falsa a promessa da política
    de privacidade sobre conteúdo de grupo de terceiro.
    """
    if not pertence_a_este_ambiente(nome_sessao):
        return
    remetente = str(payload.get("from") or "")
    if not remetente.endswith("@g.us"):
        return          # conversa privada nunca entra no monitoramento
    texto = str(payload.get("body") or "").strip()
    if not texto:
        return          # mídia sem legenda não tem o que replicar

    repo = WhatsappInstanciaRepository(db)
    instancia = repo.por_nome(nome_sessao)
    if not instancia:
        return

    from app.models.whatsapp_grupos import WhatsappGrupo
    from app.services.monitoramento_service import MonitoramentoService

    # Tudo que toca o banco fica dentro do try: exceção que escapa de um
    # webhook faz o WAHA reenviar o evento em loop.
    try:
        grupo = (
            db.query(WhatsappGrupo)
            .filter(WhatsappGrupo.jid == remetente,
                    WhatsappGrupo.user_id == instancia.user_id)
            .first()
        )
        if not grupo:
            return

        servico = MonitoramentoService(db)
        for m in servico.ativos_do_grupo(grupo.id, instancia.user_id):
            passa, link = servico.interessa(m, texto)
            if not passa:
                continue
            captura = servico.capturar(m, texto, link)
            if captura is None:
                continue        # repost da mesma oferta — dedup por constraint
            db.commit()
            if m.replicar_automaticamente:
                from app.tasks.monitoramento_tasks import replicar_captura
                # priority 0: a oferta tem validade e a afiliada está esperando.
                replicar_captura.apply_async(args=[captura.id], priority=0)
    except Exception as e:
        db.rollback()
        # Webhook nunca propaga exceção: o WAHA reenviaria o evento em loop.
        #
        # SEM traceback de propósito: o `str` de um erro do SQLAlchemy embute o
        # SQL **com os parâmetros**, ou seja, o texto da mensagem de terceiro
        # iria parar no log da aplicação — exatamente o que este módulo promete
        # que não acontece.
        logger.error("Falha ao capturar mensagem monitorada (%s)", type(e).__name__)
