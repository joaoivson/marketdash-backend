"""
Módulo de Grupos — F2: Campanhas (conjuntos de grupos). Tudo MAX-only.

Rota fina: validação Pydantic + service. Ownership como dependency — a
próxima rota da fase herda o guard em vez de lembrar de copiá-lo.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_plan
from app.core.plans import normalize_plan, plan_limit
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.campanhas_grupos import (
    CampanhaAtualizar, CampanhaCriar, CampanhaDetalheOut, CampanhaOut,
    GrupoDaCampanhaItem, GrupoDaCampanhaOut,
)
from app.services.campanha_grupos_service import (
    CampanhaGruposService, GrupoInvalido, LimiteDeCampanhas,
)
from app.services.campanha_link_service import CampanhaLinkService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["campanhas-grupos"])


def _servico(db: Session, user: User | None = None) -> CampanhaGruposService:
    """`user` só no caminho que CRIA (o único que lê o limite do plano)."""
    limite = -1
    if user is not None:
        sub = SubscriptionRepository(db).get_by_user_id(user.id)
        limite = plan_limit(normalize_plan(sub.plan if sub else None), "campanhas_grupos")
    return CampanhaGruposService(db, plan_limit_campanhas=limite)


def campanha_da_usuaria(
    campanha_id: int,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    campanha = CampanhaGruposService(db).obter(current_user.id, campanha_id)
    if not campanha:
        raise HTTPException(status_code=404, detail="Campanha não encontrada.")
    return campanha


def _out(campanha, total_grupos: int) -> CampanhaOut:
    return CampanhaOut(
        id=campanha.id, nome=campanha.nome, descricao=campanha.descricao,
        status=campanha.status, estrategia_entrada=campanha.estrategia_entrada,
        abertura_automatica=campanha.abertura_automatica,
        reabertura_automatica=campanha.reabertura_automatica,
        prefixo=campanha.prefixo, sufixo=campanha.sufixo,
        modo_imagem=campanha.modo_imagem, total_grupos=total_grupos,
        criado_em=campanha.criado_em,
    )


def _grupos_out(servico: CampanhaGruposService, campanha) -> list[GrupoDaCampanhaOut]:
    return [
        GrupoDaCampanhaOut(
            grupo_id=g.id, posicao=v.posicao, aberto=v.aberto,
            nome=g.nome, participantes=g.participantes,
            permite_envio=g.permite_envio, ativo=g.ativo, sub_id=g.sub_id,
        )
        for v, g in servico.grupos_da_campanha(campanha)
    ]


@router.get("", response_model=list[CampanhaOut])
def listar(
    incluir_arquivadas: bool = False,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    campanhas, contagens = servico.listar(current_user.id, incluir_arquivadas)
    return [_out(c, contagens.get(c.id, 0)) for c in campanhas]


@router.post("", response_model=CampanhaOut, status_code=201)
def criar(
    payload: CampanhaCriar,
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    try:
        campanha = _servico(db, current_user).criar(
            current_user.id, payload.nome, payload.descricao
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except LimiteDeCampanhas as e:
        raise HTTPException(status_code=403, detail=str(e))
    return _out(campanha, 0)


@router.get("/{campanha_id}", response_model=CampanhaDetalheOut)
def detalhe(
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    grupos = _grupos_out(servico, campanha)
    base = _out(campanha, len(grupos))
    return CampanhaDetalheOut(**base.model_dump(), grupos=grupos)


@router.patch("/{campanha_id}", response_model=CampanhaOut)
def atualizar(
    payload: CampanhaAtualizar,
    campanha=Depends(campanha_da_usuaria),
    current_user: User = Depends(require_plan("max")),
    db: Session = Depends(get_db),
):
    # Com limite: desarquivar é um "criar de volta" e re-conta a vaga do plano.
    servico = _servico(db, current_user)
    try:
        campanha = servico.atualizar(campanha, payload.model_dump(exclude_unset=True))
    except LimiteDeCampanhas as e:
        raise HTTPException(status_code=403, detail=str(e))
    total = servico.total_de_grupos(campanha)
    return _out(campanha, total)


@router.put("/{campanha_id}/grupos", response_model=CampanhaDetalheOut)
def definir_grupos(
    payload: list[GrupoDaCampanhaItem],
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = _servico(db)
    try:
        servico.definir_grupos(
            campanha, [(i.grupo_id, i.posicao, i.aberto) for i in payload]
        )
    except GrupoInvalido:
        raise HTTPException(status_code=404, detail="Grupo não encontrado.")
    grupos = _grupos_out(servico, campanha)
    base = _out(campanha, len(grupos))
    return CampanhaDetalheOut(**base.model_dump(), grupos=grupos)


# --- link de entrada (F6) ----------------------------------------------------


@router.get("/{campanha_id}/link")
def obter_link(campanha=Depends(campanha_da_usuaria), db: Session = Depends(get_db)):
    """Cria na primeira visita — a afiliada não precisa 'gerar' nada."""
    from app.core.config import settings

    link = CampanhaLinkService(db).obter_ou_criar(campanha)
    base = (settings.FRONTEND_URL or "https://marketdash.com.br").rstrip("/")
    return {
        "id": link.id,
        "slug": link.slug,
        "url": f"{base}/g/{link.slug}",
        "url_teste": f"{base}/g/preview/{link.slug}",
        "titulo_previa": link.titulo_previa,
        "descricao_previa": link.descricao_previa,
        "banner_previa_url": link.banner_previa_url,
        "pixel_facebook_id": link.pixel_facebook_id,
        "pixel_eventos": link.pixel_eventos,
        "ativo": link.ativo,
    }


@router.patch("/{campanha_id}/link")
def atualizar_link(
    payload: dict,
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    servico = CampanhaLinkService(db)
    link = servico.obter_ou_criar(campanha)
    servico.atualizar(link, payload or {})
    return obter_link(campanha=campanha, db=db)


@router.get("/{campanha_id}/atividade")
def atividade(
    limite: int = 50,
    campanha=Depends(campanha_da_usuaria),
    db: Session = Depends(get_db),
):
    """Feed de entradas e saídas dos grupos da campanha (sem dado pessoal)."""
    from app.repositories.campanha_link_repository import CampanhaLinkRepository

    servico = CampanhaGruposService(db)
    pares = servico.grupos_da_campanha(campanha)
    nomes = {g.id: g.nome for _v, g in pares}
    eventos = CampanhaLinkRepository(db).atividade(list(nomes), min(int(limite or 50), 200))
    return {
        "eventos": [
            {
                "tipo": e.tipo,
                "origem": e.origem,
                "grupo_id": e.grupo_id,
                "grupo": nomes.get(e.grupo_id),
                "quando": e.criado_em.isoformat() if e.criado_em else None,
            }
            for e in eventos
        ]
    }
