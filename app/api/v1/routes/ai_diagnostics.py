"""Endpoints do Diagnóstico IA."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_active_subscription, require_plan
from app.core.config import settings
from app.core.plans import normalize_plan
from app.db.session import get_db
from app.models.user import User
from app.repositories.ai_credit_repository import AiCreditRepository
from app.repositories.ai_diagnostic_repository import AiDiagnosticRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.ai_diagnostic import (
    DiagnosticoResponse, DiagnosticoResumo, GerarDiagnosticoRequest,
    MensagemResponse, PerguntaRequest, SaldoResponse,
)
from app.services.ai_credit_service import (
    CUSTO_CHAT, CUSTO_GERACAO, AiCreditService, SaldoInsuficiente,
)
from app.services.ai_diagnostic_service import (
    AiDiagnosticService, GeracaoEmAndamento, LimiteDeMensagens, PeriodoVazio,
)
from app.services.ai_snapshot_service import AiSnapshotService
from app.services.openai_client import ErroIA, OpenAiClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diagnostico-ia"])

# A tela oferece 7/14/30 dias. O teto existe para o caso de alguém chamar a API
# direto: período grande incha o snapshot, o custo do token e o tempo de
# resposta de uma rota síncrona — pelo mesmo preço de 10 créditos.
MAXIMO_DE_DIAS = 92


def traduzir_erro(exc: Exception) -> Optional[HTTPException]:
    """Exceção de domínio → HTTP. Devolve None quando não é erro conhecido."""
    if isinstance(exc, SaldoInsuficiente):
        return HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "SEM_CREDITOS", "saldo": exc.saldo,
                    "necessario": exc.necessario,
                    "message": "Seus créditos de IA acabaram neste mês."},
        )
    if isinstance(exc, PeriodoVazio):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Não há dados no período escolhido. Escolha outro período.",
        )
    if isinstance(exc, GeracaoEmAndamento):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma análise em andamento.",
        )
    if isinstance(exc, LimiteDeMensagens):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Esta conversa atingiu o limite. Gere um novo diagnóstico.",
        )
    if isinstance(exc, ErroIA):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A análise por IA está indisponível no momento.",
        )
    return None


def _plano(db: Session, user_id: int) -> str:
    sub = SubscriptionRepository(db).get_by_user_id(user_id)
    return normalize_plan(sub.plan if sub else None)


def _credito(db: Session) -> AiCreditService:
    return AiCreditService(AiCreditRepository(db))


def _servico(db: Session) -> AiDiagnosticService:
    return AiDiagnosticService(
        repo=AiDiagnosticRepository(db),
        cliente=OpenAiClient(settings.OPENAI_API_KEY, settings.OPENAI_MODEL),
        snapshot_svc=AiSnapshotService(db),
        credito_svc=_credito(db),
    )


def _montar_resposta(sessao, mensagens=None) -> DiagnosticoResponse:
    return DiagnosticoResponse(
        id=sessao.id,
        periodo_inicio=sessao.periodo_inicio,
        periodo_fim=sessao.periodo_fim,
        status=sessao.status,
        erro_mensagem=sessao.erro_mensagem,
        relatorio=sessao.relatorio,
        snapshot=sessao.snapshot,
        criado_em=sessao.criado_em,
        mensagens=[
            MensagemResponse(id=m.id, papel=m.papel, conteudo=m.conteudo,
                             criado_em=m.criado_em)
            for m in (mensagens or [])
        ],
    )


@router.get("/saldo", response_model=SaldoResponse)
def saldo(
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    plano = _plano(db, current_user.id)
    servico = _credito(db)
    return SaldoResponse(
        saldo=servico.saldo(current_user.id, plano),
        cota=servico.cota(plano),
        custo_geracao=CUSTO_GERACAO,
        custo_chat=CUSTO_CHAT,
        disponivel=bool(settings.OPENAI_API_KEY),
    )


@router.post("", response_model=DiagnosticoResponse, status_code=status.HTTP_201_CREATED)
def gerar(
    payload: GerarDiagnosticoRequest,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    if payload.fim < payload.inicio:
        raise HTTPException(status_code=400, detail="Período inválido.")
    if (payload.fim - payload.inicio).days + 1 > MAXIMO_DE_DIAS:
        raise HTTPException(
            status_code=400,
            detail=f"O período não pode passar de {MAXIMO_DE_DIAS} dias.",
        )
    try:
        sessao = _servico(db).gerar(
            current_user.id, _plano(db, current_user.id), payload.inicio, payload.fim
        )
    except Exception as exc:
        traduzido = traduzir_erro(exc)
        if traduzido:
            raise traduzido
        logger.exception("Falha inesperada ao gerar diagnóstico")
        raise HTTPException(status_code=500, detail="Erro ao gerar a análise.")
    return _montar_resposta(sessao)


@router.get("", response_model=List[DiagnosticoResumo])
def listar(
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    return [
        DiagnosticoResumo(
            id=s.id, periodo_inicio=s.periodo_inicio, periodo_fim=s.periodo_fim,
            status=s.status, criado_em=s.criado_em,
        )
        for s in AiDiagnosticRepository(db).listar(current_user.id)
    ]


@router.get("/{diagnostic_id}", response_model=DiagnosticoResponse)
def detalhar(
    diagnostic_id: int,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    repo = AiDiagnosticRepository(db)
    sessao = repo.buscar(diagnostic_id, current_user.id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Diagnóstico não encontrado")
    return _montar_resposta(sessao, repo.listar_mensagens(diagnostic_id))


@router.post("/{diagnostic_id}/mensagens", response_model=MensagemResponse)
def responder(
    diagnostic_id: int,
    payload: PerguntaRequest,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    try:
        m = _servico(db).responder(
            current_user.id, _plano(db, current_user.id), diagnostic_id, payload.pergunta
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        traduzido = traduzir_erro(exc)
        if traduzido:
            raise traduzido
        logger.exception("Falha inesperada no chat do diagnóstico")
        raise HTTPException(status_code=500, detail="Erro ao responder.")
    return MensagemResponse(id=m.id, papel=m.papel, conteudo=m.conteudo,
                            criado_em=getattr(m, "criado_em", None))
