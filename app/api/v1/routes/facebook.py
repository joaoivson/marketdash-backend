import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.db.session import get_db
from app.models.user import User
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.schemas.facebook_integration import (
    FacebookAdAccount,
    FacebookAdAccountSelect,
    FacebookAdAccountsSelect,
    FacebookIntegrationResponse,
    FacebookOAuthCallback,
    FacebookOAuthUrlResponse,
)
from app.services.facebook_integration_service import FacebookIntegrationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["facebook"])


def _service(db: Session) -> FacebookIntegrationService:
    return FacebookIntegrationService(FacebookIntegrationRepository(db))


@router.get("/oauth-url", response_model=FacebookOAuthUrlResponse)
def get_oauth_url(
    redirect_uri: str | None = Query(default=None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retorna a URL do diálogo de login do Facebook para o frontend redirecionar."""
    return FacebookOAuthUrlResponse(url=_service(db).build_oauth_url(redirect_uri))


@router.post("/oauth/callback", response_model=FacebookIntegrationResponse)
async def oauth_callback(
    payload: FacebookOAuthCallback,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Recebe o `code` do OAuth, troca por token long-lived e salva a integração."""
    return await _service(db).handle_oauth_callback(current_user.id, payload.code, payload.redirect_uri)


@router.get("/ad-accounts", response_model=list[FacebookAdAccount])
async def list_ad_accounts(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lista as contas de anúncio acessíveis pelo token do usuário."""
    return await _service(db).list_ad_accounts(current_user.id)


@router.put("/ad-account", response_model=FacebookIntegrationResponse)
def select_ad_account(
    payload: FacebookAdAccountSelect,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """[Legado] Seleciona UMA conta de anúncio."""
    return _service(db).select_ad_account(current_user.id, payload.ad_account_id, payload.ad_account_name)


@router.put("/ad-accounts", response_model=FacebookIntegrationResponse)
def select_ad_accounts(
    payload: FacebookAdAccountsSelect,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Seleciona uma ou mais contas de anúncio e dispara um sync imediato.

    Não há botão de sincronizar: o sync roda automaticamente ao escolher as contas
    e, depois, de hora em hora via pg_cron.
    """
    result = _service(db).select_ad_accounts(current_user.id, payload.account_ids)
    if result.ad_account_ids:
        try:
            from app.tasks.facebook_tasks import sync_facebook_user_task
            sync_facebook_user_task.apply_async(args=[current_user.id], priority=0)
        except Exception as exc:  # broker indisponível: o cron horário cobre mesmo assim
            logger.warning("Falha ao enfileirar sync após seleção de contas user_id=%s: %s", current_user.id, exc)
    return result


@router.get("/status", response_model=FacebookIntegrationResponse | None)
def get_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Status da integração Facebook (sem expor o token)."""
    return _service(db).get_status(current_user.id)


@router.get("/validate-token", response_model=dict)
def validate_token(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Verifica estado de conexão Facebook.

    Retorna:
    {
        "valid": bool,
        "status": "conectado" | "desconectado" | "nunca",
        "connection_state": igual a status (alias)
    }
    """
    svc = _service(db)
    state = svc.resolve_connection_state(current_user.id)
    return {
        "valid": state == "conectado",
        "status": state,
        "connection_state": state,
    }


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def disconnect(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a integração Facebook do usuário. Idempotente."""
    _service(db).disconnect(current_user.id)
    return None


@router.post("/clear-ads-data", response_model=dict)
def clear_ads_data(
    payload: dict,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Apaga campanhas, insights, gastos e cliques. Não toca Shopee.

    Body: { "confirm": true }
    """
    if not isinstance(payload, dict) or not payload.get("confirm"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Confirme a exclusão enviando {\"confirm\": true}",
        )
    result = _service(db).clear_ads_data(current_user.id)
    return {"status": "ok", "deleted": result}


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def manual_sync(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Enfileira a sincronização de campanhas/insights no Celery worker (202 imediato)."""
    if getattr(current_user, "is_demo", False):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conta demo não sincroniza com o Facebook.",
        )
    svc = _service(db)
    status_obj = svc.get_status(current_user.id)
    if not status_obj or not status_obj.ad_account_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conecte o Facebook e selecione ao menos uma conta de anúncio antes de sincronizar.",
        )
    from app.tasks.facebook_tasks import sync_facebook_user_task
    task = sync_facebook_user_task.apply_async(args=[current_user.id], priority=0)
    return {"status": "accepted", "task_id": task.id, "detail": "Sincronização enfileirada."}
