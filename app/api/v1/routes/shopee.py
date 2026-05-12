import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.db.session import get_db
from app.models.user import User
from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.schemas.shopee_integration import (
    ShopeeCredentialsUpsert,
    ShopeeGraphQLRequest,
    ShopeeGraphQLResponse,
    ShopeeIntegrationResponse,
)
from app.services.shopee_integration_service import ShopeeIntegrationService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["shopee"])


def _service(db: Session) -> ShopeeIntegrationService:
    return ShopeeIntegrationService(ShopeeIntegrationRepository(db))


@router.post("/credentials", response_model=ShopeeIntegrationResponse, status_code=status.HTTP_200_OK)
def save_credentials(
    payload: ShopeeCredentialsUpsert,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Salva ou atualiza credenciais Shopee do usuário."""
    return _service(db).save_credentials(current_user.id, payload.app_id, payload.password)


@router.get("/credentials", response_model=ShopeeIntegrationResponse | None)
def get_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retorna o status da integração Shopee (sem expor a senha)."""
    return _service(db).get_status(current_user.id)


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
def delete_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a integração Shopee do usuário."""
    _service(db).delete_credentials(current_user.id)
    return None


@router.post("/graphql", response_model=ShopeeGraphQLResponse)
async def proxy_graphql(
    payload: ShopeeGraphQLRequest,
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """Proxy para queries GraphQL da Shopee Affiliate API."""
    result = await _service(db).proxy_graphql(current_user.id, payload.query, payload.variables)
    return ShopeeGraphQLResponse(**result)


@router.post("/sync", status_code=status.HTTP_202_ACCEPTED)
def manual_sync(
    current_user: User = Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    """
    Enfileira sincronização Shopee no Celery worker e retorna 202 imediato.
    O sync real (88 dias em chunks + GraphQL) leva minutos; resposta síncrona
    estoura timeout de gateway (Cloudflare ~100s). Frontend deve fazer polling
    em GET /credentials.last_sync_at para detectar conclusão.
    """
    svc = _service(db)
    if not svc.get_status(current_user.id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Integração Shopee não configurada.",
        )
    from app.tasks.shopee_tasks import sync_shopee_user_task
    task = sync_shopee_user_task.delay(current_user.id, empty_attempt=0)
    return {"status": "accepted", "task_id": task.id, "detail": "Sincronização enfileirada."}
