"""
Módulo de Grupos — F5: busca de ofertas e integrações de marketplace.

A busca assina com a credencial DA ALUNA: a comissão segue a conta que assina.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_active_subscription, require_plan
from app.db.session import get_db
from app.models.user import User
from app.schemas.ofertas import (
    BuscaOfertasOut, IntegracaoAtualizar, IntegracaoCriar, IntegracaoOut,
)
from app.services.integracao_service import (
    EscolhaNecessaria, IntegracaoNaoEncontrada, IntegracaoService,
    ProvedorInvalido,
)
from app.services.oferta_service import ORDENACOES, BuscaInvalida, OfertaService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["ofertas"])


def _mascarar_app_id(app_id: str) -> str:
    app_id = app_id or ""
    return f"{app_id[:4]}•••{app_id[-2:]}" if len(app_id) > 6 else "•••"


def _integracao_out(servico: IntegracaoService, i) -> IntegracaoOut:
    try:
        app_id = servico.credenciais_de(i).get("app_id", "")
    except Exception:
        app_id = ""
    return IntegracaoOut(id=i.id, provedor=i.provedor, label=i.label,
                         ativa=i.ativa, app_id_mascarado=_mascarar_app_id(app_id),
                         criado_em=i.criado_em)


def integracao_da_usuaria(
    integracao_id: int,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    integracao = IntegracaoService(db).repo.por_id(current_user.id, integracao_id)
    if not integracao:
        raise HTTPException(status_code=404, detail="Integração não encontrada.")
    return integracao


# --- integrações (todos os planos: é a credencial da Shopee) ------------------

@router.get("/integracoes", response_model=list[IntegracaoOut])
def listar_integracoes(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    servico = IntegracaoService(db)
    return [_integracao_out(servico, i) for i in servico.listar(current_user.id)]


@router.post("/integracoes", response_model=IntegracaoOut, status_code=201)
def criar_integracao(
    payload: IntegracaoCriar,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    servico = IntegracaoService(db)
    try:
        integracao = servico.salvar(current_user.id, payload.provedor,
                                    payload.label, payload.app_id, payload.senha)
    except ProvedorInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _integracao_out(servico, integracao)


@router.patch("/integracoes/{integracao_id}", response_model=IntegracaoOut)
def atualizar_integracao(
    payload: IntegracaoAtualizar,
    integracao=Depends(integracao_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = IntegracaoService(db)
    if payload.ativa is not None:
        servico.alternar(integracao, payload.ativa)
    return _integracao_out(servico, integracao)


@router.delete("/integracoes/{integracao_id}", status_code=204)
def remover_integracao(
    integracao=Depends(integracao_da_usuaria),
    db: Session = Depends(get_db),
):
    IntegracaoService(db).remover(integracao)


# --- busca de ofertas (MAX: alimenta o módulo de grupos) ----------------------

@router.get("/ordenacoes")
def ordenacoes(_: User = Depends(require_plan("max"))):
    return {"ordenacoes": list(ORDENACOES.keys())}


@router.get("", response_model=BuscaOfertasOut)
async def buscar(
    q: str | None = None,
    categoria: str | None = None,
    ordenacao: str = "relevancia",
    pagina: int = 1,
    limite: int = 20,
    comissao_minima: float | None = None,
    preco_max: float | None = None,
    desconto_minimo: float | None = None,
    filter_integracao_id: int | None = None,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    try:
        return await OfertaService(db).buscar(
            current_user.id, keyword=q, categoria=categoria, ordenacao=ordenacao,
            pagina=pagina, limite=limite, comissao_minima=comissao_minima,
            preco_max=preco_max, desconto_minimo=desconto_minimo,
            integracao_id=filter_integracao_id,
        )
    except BuscaInvalida as e:
        raise HTTPException(status_code=422, detail=str(e))
    except EscolhaNecessaria as e:
        raise HTTPException(status_code=409,
                            detail={"escolha": e.labels, "provedor": e.provedor})
    except (IntegracaoNaoEncontrada, ProvedorInvalido) as e:
        raise HTTPException(status_code=404, detail=str(e))
    except HTTPException:
        raise
    except Exception:
        logger.exception("Busca de ofertas falhou para user %s", current_user.id)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Não conseguimos buscar ofertas agora. Tente de novo em instantes.",
        )
