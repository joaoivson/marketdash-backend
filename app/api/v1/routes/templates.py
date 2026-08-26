"""
Módulo de Grupos — F4: templates de mensagem e a IA que gera variações.

A IA vive SÓ aqui: no caminho do envio, o motor sorteia uma variação já
pronta (custo e latência zero, sem risco de inventar preço).
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.db.session import get_db
from app.models.user import User
from app.schemas.templates import (
    GerarVariacoesIn, GerarVariacoesOut, TemplateAtualizar, TemplateCriar,
    TemplateDetalheOut, TemplateOut, VariacaoIn, VariacaoOut,
)
from app.services.openai_client import ErroIA
from app.services.template_ia_service import (
    ESTILOS, TemplateIaService, TextoBaseInvalido,
)
from app.services.template_mensagem_service import (
    TemplateInvalido, TemplateMensagemService,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["templates"])


def template_da_usuaria(
    template_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    template = TemplateMensagemService(db).obter(current_user.id, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template não encontrado.")
    return template


def _out(t, variacoes) -> TemplateOut:
    return TemplateOut(id=t.id, nome=t.nome, tipo=t.tipo, ativo=t.ativo,
                       total_variacoes=len(variacoes), criado_em=t.criado_em)


def _detalhe(db: Session, t) -> TemplateDetalheOut:
    from app.repositories.template_repository import TemplateRepository

    variacoes = TemplateRepository(db).variacoes(t.id)
    base = _out(t, variacoes)
    return TemplateDetalheOut(
        **base.model_dump(exclude={"total_variacoes"}),
        total_variacoes=base.total_variacoes,
        variacoes=[VariacaoOut(id=v.id, corpo=v.corpo, peso=v.peso, ativa=v.ativa)
                   for v in variacoes],
    )


@router.get("/estilos")
def estilos_de_ia(_: User = Depends(require_plan("max"))):
    """Estilos que a tela oferece + se a IA está configurada (sem ela, a tela
    mostra o editor manual e some o botão, em vez de dar erro no clique)."""
    return {
        "disponivel": TemplateIaService().disponivel(),
        "estilos": [{"id": k, "descricao": v} for k, v in ESTILOS.items()],
    }


@router.get("", response_model=list[TemplateOut])
def listar(
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    templates, variacoes = TemplateMensagemService(db).listar(current_user.id)
    return [_out(t, variacoes.get(t.id, [])) for t in templates]


@router.post("", response_model=TemplateDetalheOut, status_code=201)
def criar(
    payload: TemplateCriar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    try:
        template = TemplateMensagemService(db).criar(
            current_user.id, payload.nome, payload.tipo
        )
    except TemplateInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _detalhe(db, template)


@router.get("/{template_id}", response_model=TemplateDetalheOut)
def detalhe(template=Depends(template_da_usuaria), db: Session = Depends(get_db)):
    return _detalhe(db, template)


@router.patch("/{template_id}", response_model=TemplateDetalheOut)
def atualizar(
    payload: TemplateAtualizar,
    template=Depends(template_da_usuaria),
    db: Session = Depends(get_db),
):
    TemplateMensagemService(db).atualizar(
        template, payload.model_dump(exclude_unset=True)
    )
    return _detalhe(db, template)


@router.put("/{template_id}/variacoes", response_model=TemplateDetalheOut)
def definir_variacoes(
    payload: list[VariacaoIn],
    template=Depends(template_da_usuaria),
    db: Session = Depends(get_db),
):
    try:
        TemplateMensagemService(db).definir_variacoes(
            template, [(v.corpo, v.peso, v.ativa) for v in payload]
        )
    except TemplateInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _detalhe(db, template)


@router.delete("/{template_id}", status_code=204)
def remover(template=Depends(template_da_usuaria), db: Session = Depends(get_db)):
    TemplateMensagemService(db).remover(template)


@router.post("/{template_id}/gerar-variacoes", response_model=GerarVariacoesOut)
def gerar_variacoes(
    payload: GerarVariacoesIn,
    template=Depends(template_da_usuaria),
    db: Session = Depends(get_db),
):
    servico_ia = TemplateIaService()
    if not servico_ia.disponivel():
        raise HTTPException(
            status_code=503,
            detail="A geração por IA está indisponível no momento.",
        )
    try:
        variacoes = servico_ia.gerar_variacoes(
            payload.texto_base, payload.estilo, payload.quantidade
        )
    except TextoBaseInvalido as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ErroIA as e:
        logger.warning("IA de variações falhou (%s)", e.motivo)
        raise HTTPException(
            status_code=502,
            detail="Não conseguimos gerar as variações agora. Tente de novo em instantes.",
        )
    salvas = 0
    if payload.salvar:
        salvas = TemplateMensagemService(db).acrescentar_variacoes(template, variacoes)
    return GerarVariacoesOut(variacoes=variacoes, salvas=salvas)
