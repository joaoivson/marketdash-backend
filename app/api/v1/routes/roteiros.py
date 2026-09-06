"""
Módulo de Grupos — F3: roteiros, envio rápido e execuções. Tudo MAX-only.

Rota fina: Pydantic + service. Ownership como dependency (padrão da F2).
"""
import logging
from datetime import timezone
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.db.session import get_db
from app.models.user import User
from app.models.roteiro import (
    EXEC_AGENDADA, EXEC_ATIVAS, EXEC_ENVIANDO, EXEC_PAUSADA, PassoBloco,
    Roteiro, RoteiroExecucao, RoteiroPasso,
)
from app.schemas.roteiros import (
    AgendarIn, AjustarDatasIn, BlocoOut, EnvioRapidoIn, ExecucaoOut,
    ExecucaoResumo, PassoIn, PassoOut, ReenviarIn, RoteiroCriar,
    RoteiroDetalheOut, RoteiroOut, StatusDoPasso,
)
from app.services.janela_envio_service import BRT
from app.services.roteiro_service import (
    CampanhaInvalida, ExecucaoJaAtiva, PassoJaEnviado, PassosNoPassado,
    RoteiroInvalido, RoteiroService, estimativa_de_duracao_s, ordens_no_passado,
    resolver_horarios, segundos_para_offset,
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


def _resumo(e: Optional[RoteiroExecucao]) -> Optional[ExecucaoResumo]:
    if e is None:
        return None
    return ExecucaoResumo(
        id=e.id, status=e.status, total=e.total, enviados=e.enviados,
        erros=e.erros, pulados=e.pulados,
        proxima_execucao_em=e.proxima_execucao_em, concluido_em=e.concluido_em,
    )


def _roteiro_out(db: Session, r: Roteiro, *, total_passos: Optional[int] = None,
                 execucoes: Optional[List[RoteiroExecucao]] = None) -> RoteiroOut:
    """`total_passos`/`execucoes` já vêm em lote quando o chamador é a LISTA —
    sem eles seriam 3 queries por linha da tela."""
    servico = RoteiroService(db)
    if execucoes is None:
        ativa, ultima = servico.execucao_ativa(r.id), servico.ultima_execucao(r.id)
    else:
        ativa = next((e for e in execucoes if e.status in EXEC_ATIVAS), None)
        ultima = execucoes[0] if execucoes else None
    return RoteiroOut(
        id=r.id, nome=r.nome, campanha_id=r.campanha_id, status=r.status,
        origem=r.origem,
        total_passos=(total_passos if total_passos is not None
                      else len(servico.repo.passos(r.id))),
        criado_em=r.criado_em,
        execucao_ativa=_resumo(ativa), ultima_execucao=_resumo(ultima),
    )


def _bloco_out(b: PassoBloco) -> BlocoOut:
    return BlocoOut(id=b.id, ordem=b.ordem, tipo=b.tipo, conteudo=b.conteudo,
                    legenda=b.legenda, template_id=b.template_id)


def _passo_out(p: RoteiroPasso, blocos: List[PassoBloco], *, quando=None,
               no_passado: bool = False, travado: bool = False,
               status: Optional[StatusDoPasso] = None) -> PassoOut:
    valor, unidade = segundos_para_offset(p.offset_segundos, p.offset_unidade)
    relativo = p.tipo_tempo == "relativo"
    return PassoOut(
        id=p.id, ordem=p.ordem, tipo_tempo=p.tipo_tempo, hora_fixa=p.hora_fixa,
        data_fixa=p.data_fixa,
        offset_valor=valor if relativo else None,
        offset_unidade=unidade if relativo else None,
        tipo_conteudo=p.tipo_conteudo,
        blocos=[_bloco_out(b) for b in blocos],
        texto=p.texto, midia_url=p.midia_url, oferta_url=p.oferta_url,
        template_id=p.template_id, acao=p.acao,
        acao_parametro=p.acao_parametro,
        acao_descontinuada=bool(p.acao_descontinuada),
        grupos_alvo=p.grupos_alvo, grupos_alvo_ids=p.grupos_alvo_ids,
        marcar_todos=p.marcar_todos, quando=quando, no_passado=no_passado,
        travado=travado, status=status,
    )


def _detalhe(db: Session, roteiro: Roteiro) -> RoteiroDetalheOut:
    """Passos com horário resolvido, trava e status da última execução.

    A linha do passo mostrava só `+5 min`. Sem o horário real ela ancora um
    `+5min` num passo de ontem e não vê que caiu no passado — e a coluna da
    direita, que resolvia isso, some quando o roteiro já está agendado.
    """
    servico = RoteiroService(db)
    passos = servico.repo.passos(roteiro.id)
    blocos = servico.repo.blocos_por_passo([p.id for p in passos])

    quando_por_id: Dict[int, object] = {}
    avisos: List[str] = []
    atrasados: List[int] = []
    if passos:
        try:
            resolvidos, avisos = resolver_horarios(passos)
            quando_por_id = {p.id: m for p, m in resolvidos}
            atrasados = ordens_no_passado(resolvidos)
        except RoteiroInvalido as e:
            # Roteiro incompleto ainda tem que ABRIR — é justamente onde ela
            # vai completar. O aviso conta o que falta.
            avisos = [str(e)]

    travados = servico.passos_intocaveis(servico.execucao_ativa(roteiro.id))
    status_bruto = servico.status_dos_passos(roteiro).get("passos", {})

    base = _roteiro_out(db, roteiro)
    return RoteiroDetalheOut(
        **base.model_dump(),
        passos=[
            _passo_out(
                p, blocos.get(p.id, []),
                quando=quando_por_id.get(p.id),
                no_passado=p.ordem in atrasados,
                travado=p.id in travados,
                status=(StatusDoPasso(**status_bruto[p.id])
                        if p.id in status_bruto else None),
            )
            for p in passos
        ],
        avisos=avisos,
        passos_no_passado=atrasados,
    )


def _execucao_out(db: Session, e, avisos=None,
                  passos: Optional[Dict[int, StatusDoPasso]] = None) -> ExecucaoOut:
    pendentes = max(e.total - e.enviados - e.erros - e.pulados, 0)
    return ExecucaoOut(
        id=e.id, roteiro_id=e.roteiro_id, data_ancora=e.data_ancora,
        status=e.status, total=e.total, enviados=e.enviados, erros=e.erros,
        pulados=e.pulados, proxima_execucao_em=e.proxima_execucao_em,
        iniciado_em=e.iniciado_em, concluido_em=e.concluido_em,
        duracao_estimada_s=estimativa_de_duracao_s(pendentes),
        avisos=avisos or [], passos=passos or {},
    )


def _erro_de_roteiro(e: RoteiroInvalido) -> HTTPException:
    """422 com corpo ESTRUTURADO quando a lista é a informação: "algum passo
    está no passado" num roteiro de 22 não diz onde clicar."""
    if isinstance(e, PassosNoPassado):
        return HTTPException(status_code=422, detail={
            "erro": "passos_no_passado", "mensagem": str(e), "passos": e.ordens,
        })
    if isinstance(e, PassoJaEnviado):
        return HTTPException(status_code=409, detail={
            "erro": "passo_ja_enviado", "mensagem": str(e), "passos": e.ordens,
        })
    if isinstance(e, ExecucaoJaAtiva):
        return HTTPException(status_code=409, detail={
            "erro": "execucao_ja_ativa", "mensagem": str(e),
            "execucao_id": e.execucao_id,
        })
    return HTTPException(status_code=422, detail=str(e))


@router.get("", response_model=list[RoteiroOut])
def listar(
    campanha_id: int | None = None,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    servico = RoteiroService(db)
    roteiros = servico.repo.por_usuario(current_user.id, campanha_id)
    ids = [r.id for r in roteiros]
    passos = servico.repo.total_de_passos(ids)
    execucoes = servico.repo.execucoes_por_roteiro(ids)
    return [_roteiro_out(db, r, total_passos=passos.get(r.id, 0),
                         execucoes=execucoes.get(r.id, []))
            for r in roteiros]


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
    try:
        servico.definir_passos(roteiro, payload.passos)
    except RoteiroInvalido as e:
        raise _erro_de_roteiro(e)
    return _detalhe(db, roteiro)


@router.get("/{roteiro_id}", response_model=RoteiroDetalheOut)
def detalhe(roteiro=Depends(roteiro_da_usuaria), db: Session = Depends(get_db)):
    return _detalhe(db, roteiro)


@router.put("/{roteiro_id}/passos", response_model=RoteiroDetalheOut)
def definir_passos(
    payload: list[PassoIn],
    roteiro=Depends(roteiro_da_usuaria),
    db: Session = Depends(get_db),
):
    """Substitui a lista de passos — preservando os ids.

    Quem manda `id` está EDITANDO aquele passo; quem manda sem `id` está
    criando. Sem essa distinção, salvar apagava e recriava os passos, e o
    CASCADE de `roteiro_mensagens.passo_id` levava junto a fila que ainda não
    tinha saído.
    """
    try:
        RoteiroService(db).definir_passos(roteiro, payload)
    except RoteiroInvalido as e:
        raise _erro_de_roteiro(e)
    return _detalhe(db, roteiro)


@router.put("/{roteiro_id}/datas", response_model=RoteiroDetalheOut)
def ajustar_datas(
    payload: AjustarDatasIn,
    roteiro=Depends(roteiro_da_usuaria),
    db: Session = Depends(get_db),
):
    """Troca as datas de vários passos de hora fixa de uma vez.

    É o que torna duplicar barato: em vez de reagendar 22 mensagens, ela ajusta
    as 4 ou 5 datas fixas e todo o resto recalcula pelo offset. Abrir modal por
    modal em 22 passos é onde o erro acontece.
    """
    try:
        RoteiroService(db).ajustar_datas(
            roteiro, {d.passo_id: (d.data_fixa, d.hora_fixa) for d in payload.datas}
        )
    except RoteiroInvalido as e:
        raise _erro_de_roteiro(e)
    return _detalhe(db, roteiro)


@router.post("/{roteiro_id}/duplicar", response_model=RoteiroOut, status_code=201)
def duplicar(roteiro=Depends(roteiro_da_usuaria), db: Session = Depends(get_db)):
    return _roteiro_out(db, RoteiroService(db).duplicar(roteiro))


@router.post("/{roteiro_id}/preview")
def preview(
    payload: AgendarIn,
    roteiro=Depends(roteiro_da_usuaria),
    db: Session = Depends(get_db),
):
    try:
        return RoteiroService(db).preview(roteiro, payload.data_ancora)
    except RoteiroInvalido as e:
        raise _erro_de_roteiro(e)


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
        raise _erro_de_roteiro(e)
    if execucao is None:
        raise HTTPException(status_code=422, detail={"avisos": avisos})
    return _execucao_out(db, execucao, avisos)


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
        raise _erro_de_roteiro(e)

    if not payload.agendar_para:
        from datetime import datetime as _dt
        from app.tasks.roteiro_tasks import processar_execucao
        execucao.status = EXEC_ENVIANDO
        execucao.iniciado_em = _dt.now(timezone.utc)
        db.commit()
        processar_execucao.apply_async(args=[execucao.id], priority=0)
    return _execucao_out(db, execucao)


@router.get("/execucoes/{execucao_id}/progresso", response_model=ExecucaoOut)
def progresso(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    servico = RoteiroService(db)
    roteiro = servico.repo.por_id(execucao.user_id, execucao.roteiro_id)
    passos = servico.status_dos_passos(roteiro)["passos"] if roteiro else {}
    return _execucao_out(db, execucao,
                         passos={k: StatusDoPasso(**v) for k, v in passos.items()})


@router.post("/execucoes/{execucao_id}/reenviar", response_model=ExecucaoOut)
def reenviar(
    payload: ReenviarIn,
    execucao=Depends(execucao_da_usuaria),
    db: Session = Depends(get_db),
):
    """Reenvia um passo aos grupos escolhidos.

    Sempre MANUAL. Retry automático mandaria a mesma mensagem duas vezes no
    grupo — erro que a afiliada vê e que o WhatsApp pune. Quem decide repetir é
    quem leu o motivo da falha.
    """
    from datetime import datetime as _dt
    from app.tasks.roteiro_tasks import processar_execucao

    servico = RoteiroService(db)
    roteiro = servico.repo.por_id(execucao.user_id, execucao.roteiro_id)
    if roteiro is None:
        raise HTTPException(status_code=404, detail="Roteiro não encontrado.")
    try:
        servico.reenviar(roteiro, execucao, payload.passo_id, payload.grupo_ids)
    except RoteiroInvalido as e:
        raise _erro_de_roteiro(e)

    # Interativo: ela está olhando a tela. priority=0 (nunca um valor do meio,
    # que cai numa fila que ninguém consome).
    execucao.status = EXEC_ENVIANDO
    execucao.iniciado_em = _dt.now(timezone.utc)
    db.commit()
    processar_execucao.apply_async(args=[execucao.id], priority=0)
    return _execucao_out(db, execucao)


@router.post("/execucoes/{execucao_id}/pausar", response_model=ExecucaoOut)
def pausar(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    if execucao.status not in (EXEC_AGENDADA, EXEC_ENVIANDO):
        raise HTTPException(status_code=409, detail="Essa execução não pode ser pausada.")
    execucao.status = EXEC_PAUSADA
    db.commit()
    return _execucao_out(db, execucao)


@router.post("/execucoes/{execucao_id}/retomar", response_model=ExecucaoOut)
def retomar(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    if execucao.status != EXEC_PAUSADA:
        raise HTTPException(status_code=409, detail="Essa execução não está pausada.")
    from datetime import datetime as _dt
    execucao.status = EXEC_AGENDADA
    execucao.proxima_execucao_em = _dt.now(timezone.utc)
    db.commit()
    return _execucao_out(db, execucao)


@router.post("/execucoes/{execucao_id}/cancelar", response_model=ExecucaoOut)
def cancelar(execucao=Depends(execucao_da_usuaria), db: Session = Depends(get_db)):
    if execucao.status in ("concluida", "cancelada"):
        raise HTTPException(status_code=409, detail="Essa execução já terminou.")
    execucao.status = "cancelada"
    db.commit()
    return _execucao_out(db, execucao)
