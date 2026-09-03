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
    ConviteAtivoOut, ConviteOut, GrupoAtualizar, GrupoOut, InstanciaAtualizar,
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
        envio_pausado=bool(i.envio_pausado),
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


def _ha_servidor_waha(db: Session) -> bool:
    """Existe alguma caixa para hospedar sessão nova? Pool primeiro, env depois."""
    from app.repositories.waha_servidor_repository import WahaServidorRepository

    try:
        if any(s.disponivel for s in WahaServidorRepository(db).listar(ativos_apenas=True)):
            return True
    except Exception:
        # Tabela ainda não existe (071 não aplicada) — cai no env.
        logger.debug("Pool de servidores WAHA indisponível", exc_info=True)
    return bool(settings.WAHA_URL and settings.WAHA_API_KEY)


@router.post("/instancias", response_model=InstanciaOut, status_code=201)
def criar_instancia(
    payload: InstanciaCriar,
    request: Request,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    # Duas fontes de servidor desde a 071: o pool e o env de antes. Exigir o env
    # travaria um ambiente 100% pool (servidores só na tabela), que é o destino
    # do desenho — o env vira legado, não pré-requisito.
    if not _ha_servidor_waha(db):
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


@router.patch("/instancias/{instancia_id}", response_model=InstanciaOut)
def atualizar_instancia(
    payload: InstanciaAtualizar,
    request: Request,
    instancia=Depends(instancia_do_usuaria),
    db: Session = Depends(get_db),
):
    """Renomear e/ou pausar o envio. Não fala com o WAHA — por isso não checa
    WAHA_URL nem devolve 503: pausar um chip precisa funcionar justamente
    quando a conexão está ruim."""
    if payload.nome_exibicao is None and payload.envio_pausado is None:
        raise HTTPException(status_code=422, detail="Nada para atualizar.")
    return _instancia_out(
        _servico(db, request).atualizar(
            instancia,
            nome_exibicao=payload.nome_exibicao,
            envio_pausado=payload.envio_pausado,
        )
    )


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


def _grupo_out(g, instancia_ids: list[int]) -> GrupoOut:
    return GrupoOut(
        id=g.id, jid=g.jid, nome=g.nome, foto_url=g.foto_url,
        participantes=g.participantes, capacidade=g.capacidade,
        sou_admin=g.sou_admin, permite_envio=g.permite_envio,
        link_convite=g.link_convite, ativo=g.ativo, ativado=bool(g.ativado),
        sub_id=g.sub_id, instancia_ids=instancia_ids,
    )


@router.get("/grupos", response_model=list[GrupoOut])
def listar_grupos(
    # `user_id` é proibido como nome de query param (fetchWithAuth injeta o
    # dele por cima); por isso `filter_instancia_id`, seguindo o admin.
    filter_instancia_id: int | None = None,
    q: str | None = None,
    incluir_inativos: bool = False,
    # Toggle da usuária — o que os pickers de campanha pedem (spec §6).
    apenas_ativados: bool = False,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    grupos, vinculos = WhatsappGrupoSyncService(db).listar(
        current_user.id,
        instancia_id=filter_instancia_id,
        busca=q,
        incluir_inativos=incluir_inativos,
        apenas_ativados=apenas_ativados,
    )
    return [_grupo_out(g, vinculos.get(g.id, [])) for g in grupos]


@router.patch("/grupos/{grupo_id}", response_model=GrupoOut)
def atualizar_grupo(
    grupo_id: int,
    payload: GrupoAtualizar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Toggle "Ativo" (spec §6.3). Ativar é o ponto de atribuição: sub_id e
    custom_link nascem AQUI, na mesma transação — o link de entrada já pode ir
    para anúncio antes de existir campanha. Desativar nunca apaga nada."""
    from app.services.whatsapp_grupo_service import (
        LimiteDeGruposAtivados, WhatsappGrupoService,
    )

    # Limite via assinatura, mesmo padrão do _servico de números acima.
    sub = SubscriptionRepository(db).get_by_user_id(current_user.id)
    limite = plan_limit(normalize_plan(sub.plan if sub else None), "whatsapp_grupos")
    servico = WhatsappGrupoService(db, plan_limit_grupos=limite)

    grupo = servico.por_id(current_user.id, grupo_id)
    if not grupo:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    try:
        grupo = servico.definir_ativado(grupo, payload.ativado)
    except LimiteDeGruposAtivados as e:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "PLANO_INSUFICIENTE",
                "message": f"Seu plano permite {e.limite} grupos ativos.",
                "limite": e.limite,
            },
        )
    return _grupo_out(grupo, servico.instancia_ids(grupo))


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
