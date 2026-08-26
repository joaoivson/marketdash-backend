"""
Módulo de Grupos — F3: roteiros, envio rápido e execuções. Tudo MAX-only.

Rota fina: Pydantic + service. Ownership como dependency (padrão da F2).
"""
import logging
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.db.session import get_db
from app.models.user import User
from app.models.roteiro import (
    EXEC_AGENDADA, EXEC_ENVIANDO, EXEC_PAUSADA, Roteiro, RoteiroPasso,
)
from app.schemas.roteiros import (
    AgendarIn, EnvioRapidoIn, ExecucaoOut, PassoOut, RoteiroCriar,
    RoteiroDetalheOut, RoteiroOut,
)
from app.services.janela_envio_service import BRT
from app.services.roteiro_service import (
    CampanhaInvalida, RoteiroInvalido, RoteiroService, estimativa_de_duracao_s,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["roteiros"])


def roteiro_da_usuaria(
    roteiro_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    roteiro = RoteiroService(db).repo.por_id(current_user.id, roteiro_id)
    if not roteiro:
        raise HTTPException(status_code=404, detail="Roteiro não encontrado.")
    return roteiro


def execucao_da_usuaria(
    execucao_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    execucao = RoteiroService(db).repo.execucao(current_user.id, execucao_id)
    if not execucao:
        raise HTTPException(status_code=404, detail="Execução não encontrada.")
    return execucao


def _roteiro_out(db: Session, r: Roteiro) -> RoteiroOut:
    total = len(RoteiroService(db).repo.passos(r.id))
    return RoteiroOut(id=r.id, nome=r.nome, campanha_id=r.campanha_id,
                      status=r.status, origem=r.origem, total_passos=total,
                      criado_em=r.criado_em)


def _passo_out(p: RoteiroPasso) -> PassoOut:
    return PassoOut(
        id=p.id, ordem=p.ordem, tipo_tempo=p.tipo_tempo, hora_fixa=p.hora_fixa,
        data_fixa=p.data_fixa, offset_minutos=p.offset_minutos,
        tipo_conteudo=p.tipo_conteudo, texto=p.texto, midia_url=p.midia_url,
        oferta_url=p.oferta_url, template_id=p.template_id, acao=p.acao,
        acao_parametro=p.acao_parametro, grupos_alvo=p.grupos_alvo,
        grupos_alvo_ids=p.grupos_alvo_ids, marcar_todos=p.marcar_todos,
    )


def _execucao_out(e, avisos=None) -> ExecucaoOut:
    pendentes = max(e.total - e.enviados - e.erros - e.pulados, 0)
    return ExecucaoOut(
        id=e.id, roteiro_id=e.roteiro_id, data_ancora=e.data_ancora,
        status=e.status, total=e.total, enviados=e.enviados, erros=e.erros,
        pulados=e.pulados, proxima_execucao_em=e.proxima_execucao_em,
        iniciado_em=e.iniciado_em, concluido_em=e.concluido_em,
        duracao_estimada_s=estimativa_de_duracao_s(pendentes),
        avisos=avisos or [],
    )


@router.get("", response_model=list[RoteiroOut])
def listar(
    campanha_id: int | None = None,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    servico = RoteiroService(db)
    return [_roteiro_out(db, r)
            for r in servico.repo.por_usuario(current_user.id, campanha_id)]


@router.post("", response_model=RoteiroDetalheOut, status_code=201)
def criar(
    payload: RoteiroCriar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    servico = RoteiroService(db)
    try:
        servico.validar_campanha(current_user.id, payload.campanha_id)
    except CampanhaInvalida as e:
        raise HTTPException(status_code=404, detail=str(e))
    roteiro = servico.repo.adicionar(Roteiro(
        user_id=current_user.id,
        campanha_id=payload.campanha_id,
        nome=payload.nome.strip()[:120],
    ))
    servico.definir_passos(roteiro, payload.passos)
    base = _roteiro_out(db, roteiro)
    return RoteiroDetalheOut(**base.model_dump(exclude={"total_passos"}),
                             total_passos=base.total_passos,
                             passos=[_passo_out(p) for p in servico.repo.passos(roteiro.id)])


@router.get("/{roteiro_id}", response_model=RoteiroDetalheOut)
def detalhe(roteiro=Depends(roteiro_da_usuaria), db: Session = Depends(get_db)):
    servico = RoteiroService(db)
    base = _roteiro_out(db, roteiro)
    return RoteiroDetalheOut(**base.model_dump(exclude={"total_passos"}),
                             total_passos=base.total_passos,
                             passos=[_passo_out(p) for p in servico.repo.passos(roteiro.id)])


@router.put("/{roteiro_id}/passos", response_model=RoteiroDetalheOut)
def definir_passos(
    payload: list,
    roteiro=Depends(roteiro_da_usuaria),
    db: Session = Depends(get_db),
):
    from app.schemas.roteiros import PassoIn
    passos = [PassoIn.model_validate(p) for p in payload]
    servico = RoteiroService(db)
    servico.definir_passos(roteiro, passos)
    base = _roteiro_out(db, roteiro)
    return RoteiroDetalheOut(**base.model_dump(exclude={"total_passos"}),
                             total_passos=base.total_passos,
                             passos=[_passo_out(p) for p in servico.repo.passos(roteiro.id)])


@router.post("/{roteiro_id}/duplicar", response_model=RoteiroOut, status_code=201)
def duplicar(roteiro=Depends(roteiro_da_usuaria), db: Session = Depends(get_db)):
    servico = RoteiroService(db)
    return _roteiro_out(db, servico.duplicar(roteiro))


@router.post("/{roteiro_id}/preview")
def preview(
    payload: AgendarIn,
    roteiro=Depends(roteiro_da_usuaria),
    db: Session = Depends(get_db),
):
    try:
        return RoteiroService(db).preview(roteiro, payload.data_ancora)
    except RoteiroInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.post("/{roteiro_id}/agendar", response_model=ExecucaoOut, status_code=201)
def agendar(
    payload: AgendarIn,
    roteiro=Depends(roteiro_da_usuaria),
    db: Session = Depends(get_db),
):
    try:
        execucao, avisos = RoteiroService(db).agendar(
            roteiro, payload.data_ancora, ignorar_avisos=payload.ignorar_avisos
        )
    except RoteiroInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    if execucao is None:
        raise HTTPException(status_code=422, detail={"avisos": avisos})
    return _execucao_out(execucao, avisos)


@router.post("/envio-rapido", response_model=ExecucaoOut, status_code=201)
def envio_rapido(
    payload: EnvioRapidoIn,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    """Atalho do dia a dia: cria o roteiro de 1 passo e agenda. 'Agora'
    enfileira IMEDIATAMENTE, sem esperar o tick de 5min."""
    servico = RoteiroService(db)
    try:
        servico.validar_campanha(current_user.id, payload.campanha_id)
        roteiro = servico.criar_envio_rapido(
            current_user.id, payload.texto, payload.midia_url,
            payload.oferta_url, payload.grupo_ids, payload.campanha_id,
        )
        quando = payload.agendar_para
        if quando is not None and quando.tzinfo is None:
            # Datetime sem fuso vindo do cliente é o horário que a USUÁRIA vê
            # — BRT. Interpretar como UTC adiantaria o disparo em 3 horas.
            quando = quando.replace(tzinfo=BRT)
        data_ancora = (quando.astimezone(BRT).date() if quando
                       else roteiro.criado_em.astimezone(BRT).date())
        if quando:
            passo = servico.repo.passos(roteiro.id)[0]
            passo.hora_fixa = quando.astimezone(BRT).time()
            passo.data_fixa = quando.astimezone(BRT).date()
            db.commit()
        execucao, _ = servico.agendar(roteiro, data_ancora, ignorar_avisos=True)
    except CampanhaInvalida as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RoteiroInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))

    if not payload.agendar_para:
        from datetime import datetime as _dt
        from app.tasks.roteiro_tasks import processar_execucao
        execucao.status = EXEC_ENVIANDO
        execucao.iniciado_em = _dt.now(timezone.utc)
        db.commit()
        processar_execucao.apply_async(args=[execucao.id], priority=0)
    return _execucao_out(execucao)


@router.get("/execucoes/{execucao_id}/progresso", response_model=ExecucaoOut)
def progresso(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    return _execucao_out(execucao)


@router.post("/execucoes/{execucao_id}/pausar", response_model=ExecucaoOut)
def pausar(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    if execucao.status not in (EXEC_AGENDADA, EXEC_ENVIANDO):
        raise HTTPException(status_code=409, detail="Essa execução não pode ser pausada.")
    execucao.status = EXEC_PAUSADA
    db.commit()
    return _execucao_out(execucao)


@router.post("/execucoes/{execucao_id}/retomar", response_model=ExecucaoOut)
def retomar(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    if execucao.status != EXEC_PAUSADA:
        raise HTTPException(status_code=409, detail="Essa execução não está pausada.")
    from datetime import datetime as _dt
    execucao.status = EXEC_AGENDADA
    execucao.proxima_execucao_em = _dt.now(timezone.utc)
    db.commit()
    return _execucao_out(execucao)


@router.post("/execucoes/{execucao_id}/cancelar", response_model=ExecucaoOut)
def cancelar(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    if execucao.status in ("concluida", "cancelada"):
        raise HTTPException(status_code=409, detail="Essa execução já terminou.")
    execucao.status = "cancelada"
    db.commit()
    return _execucao_out(execucao)
