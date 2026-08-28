"""
Módulo de Grupos — F1: números da AFILIADA (sessões WAHA) e seus grupos.

Tudo aqui é require_plan("max"). O webhook multi-sessão fica em whatsapp.py
(rota única para eventos do WAHA, roteada pelo nome da sessão).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.core.config import settings
from app.core.plans import normalize_plan, plan_limit
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository
from app.schemas.whatsapp_conexoes import (
    BlacklistCriar, BlacklistOut, ConviteAtivoOut, ConviteOut, GrupoOut,
    InstanciaCriar, InstanciaOut, InstanciaQrOut, SincronizarOut,
)
from app.services.waha_client import ErroWhatsapp, mascarar
from app.services.whatsapp_grupo_sync_service import WhatsappGrupoSyncService
from app.services.whatsapp_instancia_service import (
    LimiteDeNumeros, LimiteGlobal, WhatsappInstanciaService,
)
from app.api.v1.routes.whatsapp import url_do_webhook

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp-conexoes"])

INDISPONIVEL = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="A conexão de números está indisponível no momento.",
)


def _webhook_url(request) -> str:
    return settings.WAHA_WEBHOOK_URL or url_do_webhook(request)


def _servico(db: Session, request, user: User | None = None) -> WhatsappInstanciaService:
    """`user` só é necessário no caminho que CRIA (o único que lê o limite do
    plano) — os demais não pagam a consulta de assinatura por chamada."""
    limite = 0
    if user is not None:
        sub = SubscriptionRepository(db).get_by_user_id(user.id)
        limite = plan_limit(normalize_plan(sub.plan if sub else None), "whatsapp_numeros")
    return WhatsappInstanciaService(
        repo=WhatsappInstanciaRepository(db),
        plan_limit_numeros=limite,
        webhook_url=_webhook_url(request),
    )


def instancia_do_usuaria(
    instancia_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Ownership como dependency: nenhuma rota de instância existe sem ela —
    a próxima rota da F2/F3 herda o guard em vez de lembrar de copiá-lo."""
    instancia = WhatsappInstanciaRepository(db).por_id(current_user.id, instancia_id)
    if not instancia:
        raise HTTPException(status_code=404, detail="Número não encontrado.")
    return instancia


def _instancia_out(i) -> InstanciaOut:
    return InstanciaOut(
        id=i.id,
        nome_exibicao=i.nome_exibicao,
        numero_mascarado=mascarar(i.numero) if i.numero else None,
        status=i.status,
        ultima_conexao_em=i.ultima_conexao_em,
        criado_em=i.criado_em,
    )


@router.get("/instancias", response_model=list[InstanciaOut])
def listar_instancias(
    request: Request,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    servico = _servico(db, request)
    return [_instancia_out(i) for i in servico.listar(current_user.id)]


@router.post("/instancias", response_model=InstanciaOut, status_code=201)
def criar_instancia(
    payload: InstanciaCriar,
    request: Request,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    if not (settings.WAHA_URL and settings.WAHA_API_KEY):
        raise INDISPONIVEL
    try:
        instancia = _servico(db, request, current_user).criar(current_user.id, payload.nome_exibicao)
    except LimiteDeNumeros as e:
        raise HTTPException(status_code=403, detail=str(e))
    except LimiteGlobal as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ErroWhatsapp as e:
        if e.motivo == "sem_proxy":
            # Pool de IPs esgotado. Para a afiliada isso é CAPACIDADE — a
            # palavra "proxy" não aparece na tela dela em lugar nenhum.
            logger.error("Pool de proxies esgotado ao criar número do user %s",
                         current_user.id)
            raise HTTPException(status_code=503, detail=e.detalhe)
        logger.warning("Criar sessão falhou para user %s (%s)", current_user.id, e.motivo)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não conseguimos preparar a conexão agora. Tente de novo em instantes.",
        )
    return _instancia_out(instancia)


@router.get("/instancias/{instancia_id}/qr", response_model=InstanciaQrOut)
def qr_da_instancia(
    request: Request,
    instancia=Depends(instancia_do_usuaria),
    db: Session = Depends(get_db),
):
    return InstanciaQrOut(**_servico(db, request).qr(instancia))


@router.delete("/instancias/{instancia_id}", status_code=204)
def remover_instancia(
    request: Request,
    instancia=Depends(instancia_do_usuaria),
    db: Session = Depends(get_db),
):
    _servico(db, request).remover(instancia)


@router.post("/instancias/{instancia_id}/sincronizar-grupos", response_model=SincronizarOut)
def sincronizar_grupos(
    instancia=Depends(instancia_do_usuaria),
    db: Session = Depends(get_db),
):
    try:
        resultado = WhatsappGrupoSyncService(db).sincronizar(instancia)
    except ErroWhatsapp as e:
        if e.motivo in ("desconectado", "sessao"):
            raise HTTPException(
                status_code=409,
                detail="Esse número não está conectado. Escaneie o QR e tente de novo.",
            )
        logger.warning("Sync de grupos falhou (%s): %s", instancia.nome_instancia, e.motivo)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não conseguimos listar os grupos agora. Tente de novo em instantes.",
        )
    return SincronizarOut(**resultado)


@router.get("/config-envio")
def obter_config_envio(
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.janela_envio_service import config_da_usuaria

    return config_da_usuaria(db, current_user.id).model_dump(mode="json")


@router.put("/config-envio")
def salvar_config_envio(
    payload: dict,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.janela_envio_service import salvar_config_da_usuaria

    try:
        return salvar_config_da_usuaria(db, current_user.id, payload).model_dump(mode="json")
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        raise HTTPException(status_code=422, detail="Configuração de janela inválida.")


@router.get("/grupos", response_model=list[GrupoOut])
def listar_grupos(
    # `user_id` é proibido como nome de query param (fetchWithAuth injeta o
    # dele por cima); por isso `filter_instancia_id`, seguindo o admin.
    filter_instancia_id: int | None = None,
    q: str | None = None,
    incluir_inativos: bool = False,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    grupos, vinculos = WhatsappGrupoSyncService(db).listar(
        current_user.id,
        instancia_id=filter_instancia_id,
        busca=q,
        incluir_inativos=incluir_inativos,
    )
    return [
        GrupoOut(
            id=g.id, jid=g.jid, nome=g.nome, foto_url=g.foto_url,
            participantes=g.participantes, capacidade=g.capacidade,
            sou_admin=g.sou_admin, permite_envio=g.permite_envio,
            link_convite=g.link_convite, ativo=g.ativo, sub_id=g.sub_id,
            instancia_ids=vinculos.get(g.id, []),
        )
        for g in grupos
    ]


# --- item 17: blacklist de números -------------------------------------------


@router.get("/blacklist", response_model=list[BlacklistOut])
def listar_blacklist(
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.blacklist_service import BlacklistService

    return BlacklistService(db).listar(current_user.id)


@router.post("/blacklist", response_model=BlacklistOut, status_code=201)
def adicionar_na_blacklist(
    payload: BlacklistCriar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.blacklist_service import BlacklistService, NumeroInvalido

    try:
        item = BlacklistService(db).adicionar(
            current_user.id, payload.numero, payload.motivo,
            payload.remover_dos_grupos,
        )
    except NumeroInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    db.commit()
    return item


@router.delete("/blacklist/{item_id}", status_code=204)
def remover_da_blacklist(
    item_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.blacklist_service import BlacklistService

    if not BlacklistService(db).remover(current_user.id, item_id):
        raise HTTPException(status_code=404, detail="Número não encontrado na lista.")
    db.commit()
    return None


# --- item 18: link de conexão externa ----------------------------------------


@router.get("/instancias/{instancia_id}/convites", response_model=list[ConviteAtivoOut])
def convites_ativos(
    instancia=Depends(instancia_do_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.conexao_convite_service import ConexaoConviteService

    return ConexaoConviteService(db).ativos_da_instancia(current_user.id, instancia.id)


@router.post("/instancias/{instancia_id}/convites", response_model=ConviteOut,
             status_code=201)
def criar_convite(
    request: Request,
    instancia=Depends(instancia_do_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """
    Link temporário para OUTRA pessoa escanear o QR deste número.

    Criar um novo revoga os anteriores desta sessão: dois links vivos para o
    mesmo número significam que o primeiro — que ela talvez tenha mandado no
    grupo errado — continua funcionando.
    """
    from app.services.conexao_convite_service import ConexaoConviteService

    servico = ConexaoConviteService(db)
    convite, token = servico.criar(current_user.id, instancia.id)
    db.commit()
    return ConviteOut(id=convite.id, url=servico.url_publica(token),
                      expira_em=convite.expira_em)


@router.delete("/convites/{convite_id}", status_code=204)
def revogar_convite(
    convite_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    from app.services.conexao_convite_service import ConexaoConviteService

    if not ConexaoConviteService(db).revogar(current_user.id, convite_id):
        raise HTTPException(status_code=404, detail="Link não encontrado.")
    db.commit()
    return None
