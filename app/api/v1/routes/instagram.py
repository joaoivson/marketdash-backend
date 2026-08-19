"""Rotas da automação de Instagram (comentário → direct).

Gate de plano: a feature é exclusiva do MAX e o bloqueio é do BACKEND, não só da
UI — `require_plan("max")` em toda rota de automação. Bater direto na URL com
plano Essencial devolve 403 PLANO_INSUFICIENTE.

A CONEXÃO em si (status/conectar/desconectar) fica fora do gate de propósito: se
a assinatura cair de MAX para Pro, a aluna precisa continuar conseguindo ver e
remover a conexão que já criou.
"""

import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription, require_plan
from app.db.session import get_db
from app.models.user import User
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import (
    InstagramAuthUrlResponse,
    InstagramAutomationCreate,
    InstagramAutomationResponse,
    InstagramAutomationStatusUpdate,
    InstagramAutomationUpdate,
    InstagramConnectionResponse,
    InstagramMediaPage,
    InstagramOAuthCallback,
)
from app.services.instagram_automation_service import InstagramAutomationService
from app.services.instagram_connection_service import InstagramConnectionService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["instagram"])

# Dependency única do gate — trocar o plano exigido aqui vale pra todas as rotas.
exige_plano_max = require_plan("max")


def _conexao(db: Session) -> InstagramConnectionService:
    return InstagramConnectionService(InstagramAutomationRepository(db))


def _automacoes(db: Session) -> InstagramAutomationService:
    return InstagramAutomationService(InstagramAutomationRepository(db))


# --------------------------------------------------------------------------- #
#  Conexão                                                                     #
# --------------------------------------------------------------------------- #


@router.get("/auth-url", response_model=InstagramAuthUrlResponse)
def get_auth_url(
    redirect_uri: str | None = Query(default=None),
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """URL do Business Login for Instagram."""
    return InstagramAuthUrlResponse(url=_conexao(db).build_authorize_url(redirect_uri))


@router.post("/oauth/callback", response_model=InstagramConnectionResponse)
async def oauth_callback(
    payload: InstagramOAuthCallback,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Troca o code por token longo (60 dias) e grava a conexão."""
    return await _conexao(db).handle_oauth_callback(
        current_user.id, payload.code, payload.redirect_uri
    )


@router.get("/connection", response_model=InstagramConnectionResponse | None)
def get_connection(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Estado da conexão: ativo | expirado | revogado. `null` = nunca conectou."""
    return _conexao(db).get_status(current_user.id)


@router.post("/connection/subscribe", response_model=InstagramConnectionResponse)
async def reassinar_webhook(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Refaz a inscrição da conta no webhook de comentários.

    Existe porque a inscrição pode falhar por motivos que a aluna resolve sozinha
    (perfil privado, permissão revogada) — e sem ela nada dispara, em silêncio.
    """
    return await _conexao(db).assinar_webhook_do_usuario(current_user.id)


@router.delete("/connection", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Desconecta e remove automações e histórico. Idempotente."""
    _conexao(db).disconnect(current_user.id)
    return None


# --------------------------------------------------------------------------- #
#  Publicações                                                                 #
# --------------------------------------------------------------------------- #


@router.get("/media", response_model=InstagramMediaPage)
async def listar_midias(
    cursor: str | None = Query(default=None, description="Cursor de 'Carregar mais'"),
    refresh: bool = Query(default=False, description="Ignora o cache de 15 min"),
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    """Publicações do perfil para a grade de seleção."""
    return await _automacoes(db).listar_midias(current_user.id, cursor=cursor, forcar=refresh)


# --------------------------------------------------------------------------- #
#  Automações                                                                  #
# --------------------------------------------------------------------------- #


@router.get("/automations", response_model=list[InstagramAutomationResponse])
def listar_automacoes(
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    return _automacoes(db).listar(current_user.id)


@router.post(
    "/automations",
    response_model=InstagramAutomationResponse,
    status_code=status.HTTP_201_CREATED,
)
def criar_automacao(
    payload: InstagramAutomationCreate,
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    return _automacoes(db).criar(current_user.id, payload)


@router.get("/automations/{automation_id}", response_model=InstagramAutomationResponse)
def obter_automacao(
    automation_id: int,
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    return _automacoes(db).obter(current_user.id, automation_id)


@router.put("/automations/{automation_id}", response_model=InstagramAutomationResponse)
def atualizar_automacao(
    automation_id: int,
    payload: InstagramAutomationUpdate,
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    return _automacoes(db).atualizar(current_user.id, automation_id, payload)


@router.patch("/automations/{automation_id}/status", response_model=InstagramAutomationResponse)
def alterar_status(
    automation_id: int,
    payload: InstagramAutomationStatusUpdate,
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    """Toggle Ativa/Pausada da lista. Sem confirmação — é reversível."""
    return _automacoes(db).alterar_status(current_user.id, automation_id, payload.status)


@router.post(
    "/automations/{automation_id}/duplicate",
    response_model=InstagramAutomationResponse,
    status_code=status.HTTP_201_CREATED,
)
def duplicar_automacao(
    automation_id: int,
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    return _automacoes(db).duplicar(current_user.id, automation_id)


@router.delete("/automations/{automation_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_automacao(
    automation_id: int,
    current_user: User = Depends(exige_plano_max),
    db: Session = Depends(get_db),
):
    _automacoes(db).excluir(current_user.id, automation_id)
    return None
