"""
Monitoramento de grupos — F8. Tudo MAX-only.

Ligar/desligar um monitoramento é a única ação do produto que muda o que a
sessão do WhatsApp escuta: com monitoramento ativo a sessão passa a assinar
`message`; sem nenhum, para de assinar. Por isso o toggle não é um `UPDATE` —
ele fala com o WAHA, e pode recusar (ver `EnvioEmAndamento`).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.core.config import settings
from app.core.plans import normalize_plan, plan_limit
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.api.v1.routes.whatsapp import url_do_webhook
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository
from app.schemas.monitoramentos import (
    CapturasOut, MonitoramentoAtualizar, MonitoramentoCriar, MonitoramentoOut,
)
from app.services.monitoramento_service import (
    LimiteDeMonitoramentos, MonitoramentoInvalido, MonitoramentoService,
)
from app.services.waha_client import ErroWhatsapp
from app.services.whatsapp_instancia_service import EnvioEmAndamento, sincronizar_todas

logger = logging.getLogger(__name__)

router = APIRouter(tags=["monitoramentos"])


def _servico(db: Session, user: User | None = None) -> MonitoramentoService:
    limite = 0
    if user is not None:
        sub = SubscriptionRepository(db).get_by_user_id(user.id)
        limite = plan_limit(normalize_plan(sub.plan if sub else None), "monitoramentos")
    return MonitoramentoService(db, plan_limit_monitoramentos=limite)


def _do_usuario(
    monitoramento_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    m = MonitoramentoService(db).obter(current_user.id, monitoramento_id)
    if not m:
        raise HTTPException(status_code=404, detail="Monitoramento não encontrado.")
    return m


def _out(db: Session, m) -> MonitoramentoOut:
    resumo = MonitoramentoService(db).resumo(m)
    return MonitoramentoOut(
        id=m.id, nome=m.nome, grupo_origem_id=m.grupo_origem_id,
        grupo_origem=resumo["grupo_origem"],
        instancia_id=m.instancia_id, destino_campanha_id=m.destino_campanha_id,
        destino_grupo_ids=m.destino_grupo_ids, ativo=m.ativo,
        converter_links=m.converter_links, somente_com_link=m.somente_com_link,
        palavras_chave=m.palavras_chave,
        replicar_automaticamente=m.replicar_automaticamente,
        total_capturas=resumo["total_capturas"], criado_em=m.criado_em,
    )


class NaoDeuParaConfirmar(Exception):
    """As sessões não responderam — não dá para afirmar o que elas escutam."""


def _sincronizar_sessoes(db: Session, user_id: int, request,
                         exigir_confirmacao: bool = False) -> None:
    """Alinha o que cada sessão escuta com o estado dos monitoramentos.

    Deixar de desassinar `message` é o defeito grave aqui: a sessão seguiria
    entregando conteúdo de grupo ao backend depois de a afiliada desligar o
    monitoramento — exatamente o que a política de privacidade diz que não
    acontece.
    """
    repo = WhatsappInstanciaRepository(db)
    precisa = MonitoramentoService(db).sessoes_que_precisam_de_message(user_id)
    # Tudo-ou-nada: com dois números, reconfigurar um e recusar o outro deixaria
    # o banco divergindo do que as sessões realmente escutam.
    # A env é opcional por design; sem ela a URL vem da request, como em
    # whatsapp_conexoes. Passar "" apagaria o webhook inteiro da sessão.
    _feitas, desconhecidas = sincronizar_todas(
        db, repo.por_usuario(user_id), precisa,
        settings.WAHA_WEBHOOK_URL or url_do_webhook(request),
    )
    if desconhecidas and exigir_confirmacao:
        # Só no sentido DESLIGAR. Responder 200 aqui diria que o monitoramento
        # parou quando a sessão pode seguir assinando `message` — e a promessa
        # da política de privacidade depende exatamente disso.
        raise NaoDeuParaConfirmar(
            "Não conseguimos falar com o WhatsApp para desligar o monitoramento. "
            "Ele continua ligado — tente de novo em instantes."
        )


@router.get("", response_model=list[MonitoramentoOut])
def listar(
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    return [_out(db, m) for m in MonitoramentoService(db).listar(current_user.id)]


@router.post("", response_model=MonitoramentoOut, status_code=201)
def criar(
    payload: MonitoramentoCriar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    try:
        m = _servico(db, current_user).criar(
            current_user.id, payload.nome, payload.grupo_origem_id,
            destino_campanha_id=payload.destino_campanha_id,
            destino_grupo_ids=payload.destino_grupo_ids,
            converter_links=payload.converter_links,
            somente_com_link=payload.somente_com_link,
            palavras_chave=payload.palavras_chave,
            replicar_automaticamente=payload.replicar_automaticamente,
        )
    except MonitoramentoInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LimiteDeMonitoramentos as e:
        raise HTTPException(status_code=403, detail=str(e))
    db.commit()
    return _out(db, m)


@router.patch("/{monitoramento_id}", response_model=MonitoramentoOut)
def atualizar(
    payload: MonitoramentoAtualizar,
    request: Request,
    m=Depends(_do_usuario),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    dados = payload.model_dump(exclude_unset=True)
    # `exclude_unset` mantém a chave quando o cliente mandou `null` explícito —
    # e `setattr(m, "nome", None)` numa coluna NOT NULL vira 500 no commit.
    # Campo obrigatório com null = "não mexer".
    for campo in ("nome", "ativo", "converter_links", "somente_com_link",
                  "replicar_automaticamente"):
        if dados.get(campo) is None:
            dados.pop(campo, None)
    if "nome" in dados:
        dados["nome"] = str(dados["nome"]).strip()[:120]
        if not dados["nome"]:
            raise HTTPException(status_code=422, detail="Dê um nome ao monitoramento.")
    destinos = dados.get("destino_grupo_ids")
    if destinos and m.grupo_origem_id in destinos:
        raise HTTPException(
            status_code=422,
            detail="O grupo de origem não pode ser destino da replicação.",
        )
    # O PATCH também recebe ids crus do cliente — mesma checagem de dono do POST.
    try:
        MonitoramentoService(db).validar_destinos(
            current_user.id,
            dados.get("destino_campanha_id", m.destino_campanha_id),
            destinos,
        )
    except MonitoramentoInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    anteriores = {campo: getattr(m, campo) for campo in dados}
    for campo, valor in dados.items():
        setattr(m, campo, valor)
    db.add(m)
    db.commit()

    # Só o toggle de `ativo` muda o que a sessão escuta.
    if "ativo" in dados:
        try:
            # Desligar exige confirmação; ligar não: se a sessão não responder,
            # o monitoramento fica ligado no banco sem capturar nada, o que é
            # inofensivo e o cron diário conserta.
            _sincronizar_sessoes(db, current_user.id, request,
                                 exigir_confirmacao=dados["ativo"] is False)
        except (EnvioEmAndamento, NaoDeuParaConfirmar, ErroWhatsapp) as e:
            # Restaura os valores ANTERIORES — não `not m.ativo`. Inverter só
            # acerta quando o PATCH mudou o campo; com `ativo: false` mandado
            # sobre um monitoramento já desligado, o "desfazer" o LIGAVA, e o
            # cron do dia seguinte passaria a capturar sem ninguém ter pedido.
            # E devolver 409 com os OUTROS campos já aplicados seria erro com
            # gravação pela metade.
            for campo, valor in anteriores.items():
                setattr(m, campo, valor)
            db.add(m)
            db.commit()
            motivo = (str(e) if isinstance(e, (EnvioEmAndamento, NaoDeuParaConfirmar))
                      else "Não foi possível falar com o WhatsApp agora. "
                           "Tente de novo em instantes.")
            raise HTTPException(status_code=409, detail=motivo)
    return _out(db, m)


@router.get("/{monitoramento_id}/capturas", response_model=CapturasOut)
def capturas(
    m=Depends(_do_usuario),
    db: Session = Depends(get_db),
):
    from app.schemas.monitoramentos import CapturaOut

    return {"capturas": [CapturaOut.model_validate(c, from_attributes=True)
                         for c in MonitoramentoService(db).capturas(m.id)]}


@router.post("/{monitoramento_id}/capturas/{captura_id}/replicar")
def replicar(
    captura_id: int,
    m=Depends(_do_usuario),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Replicação manual — o caminho padrão, já que `replicar_automaticamente`
    nasce desligado."""
    from app.models.monitoramento import CAPTURA_CAPTURADA, CAPTURA_ERRO
    from app.tasks.monitoramento_tasks import replicar_captura

    servico = MonitoramentoService(db)
    captura = servico.captura_de(m.id, captura_id)
    if not captura:
        raise HTTPException(status_code=404, detail="Captura não encontrada.")
    if captura.status == CAPTURA_ERRO:
        # Erro é recuperável: a causa costuma ser passageira (Shopee fora do ar,
        # rate limit). Sem reabrir, a oferta morria em definitivo — o repost da
        # mesma mensagem cai na dedup e nunca vira captura nova.
        servico.reabrir(captura)
        db.commit()
    elif captura.status != CAPTURA_CAPTURADA:
        raise HTTPException(status_code=409,
                            detail=f"Esta captura já está como “{captura.status}”.")
    # priority 0: a afiliada clicou e está esperando.
    replicar_captura.apply_async(args=[captura.id], priority=0)
    return {"ok": True}


@router.delete("/{monitoramento_id}", status_code=204)
def remover(
    request: Request,
    m=Depends(_do_usuario),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    MonitoramentoService(db).remover(m)
    db.commit()
    # Removido o último monitoramento da sessão, ela para de assinar `message`.
    try:
        _sincronizar_sessoes(db, current_user.id, request)
    except (EnvioEmAndamento, NaoDeuParaConfirmar, ErroWhatsapp):
        # O monitoramento já foi embora; a sessão fica com um evento a mais até
        # o próximo alinhamento. O cron de reconciliação repara.
        logger.info("Sessão não reconfigurada agora (envio em andamento)")
    return None
