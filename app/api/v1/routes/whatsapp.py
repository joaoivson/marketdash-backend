"""
Webhook ÚNICO de eventos do WAHA — multi-sessão, roteado pelo nome.

É a única rota aqui, e não tem sessão de usuária — quem chama é o WAHA.
Ela se autentica por token no header e devolve 200 em qualquer caso: erro
aqui faz o WAHA re-tentar, e reprocessar o mesmo evento não ajuda ninguém.

(O resumo diário por WhatsApp morava neste módulo e foi removido por completo
na rodada de correções — spec §9.1. Ficam o webhook e seus handlers, que
servem o Módulo de Grupos: status de sessão, participantes e monitoramento.)
"""
import hmac
import logging

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository
from app.services.waha_client import campo, numero_de_jid
from app.services.whatsapp_instancia_service import (
    aplicar_evento_de_status, pertence_a_este_ambiente,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp"])


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


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def webhook(
    request: Request,
    db: Session = Depends(get_db),
    x_webhook_token: str | None = Header(default=None, alias="X-Webhook-Token"),
):
    """
    Eventos do WAHA — TODAS as sessões (números das afiliadas).

    Roteamento pelo nome da sessão: só tratamos sessões com o prefixo DESTE
    ambiente (mkd{ref4}). Evento de sessão alheia (outro ambiente no mesmo
    servidor WAHA) é ignorado em silêncio — tratar seria fratricídio hml×prod.
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
        # Sessão do resumo LEGADO: pode seguir viva no WAHA até a ops removê-la
        # — sem este guard, cada evento dela viraria warning de "desconhecida".
        logger.info("Sessão do resumo (legado): %s", status_waha)
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
    `message` só chega de sessão de aluna COM monitoramento ativo (F8) —
    sessão sem monitoramento sequer assina o evento, então o conteúdo dos
    grupos dela não chega aqui. (O SAIR do resumo diário saiu com a feature.)
    """
    if payload.get("fromMe"):
        return
    _tratar_mensagem_de_monitoramento(db, nome_sessao, payload)


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
