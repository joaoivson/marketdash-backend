"""
Endpoints internos chamados por scheduler externo (pg_cron + pg_net no Supabase).

Não usam Supabase Auth — autenticação por secret compartilhado em header X-Cron-Secret.
"""
import hmac
import logging

from fastapi import APIRouter, Header, HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


def _validate_cron_secret(received: str | None, caller_ip: str | None) -> None:
    if not settings.CRON_SECRET:
        logger.error("CRON_SECRET não configurado — rejeitando chamada interna (caller=%s)", caller_ip)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cron endpoint disabled (CRON_SECRET not configured).",
        )
    if not received or not hmac.compare_digest(received, settings.CRON_SECRET):
        logger.warning("Tentativa inválida no /internal/cron (caller=%s)", caller_ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid cron secret.",
        )


@router.post("/cron/shopee-sync", status_code=status.HTTP_202_ACCEPTED)
def cron_shopee_sync(
    request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Disparado pelo pg_cron via pg_net (10h UTC = 7h BRT).

    Enfileira sync_all_shopee_users_task no Celery worker e retorna imediatamente.
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(x_cron_secret, caller_ip)

    from app.tasks.shopee_tasks import sync_all_shopee_users_task

    task = sync_all_shopee_users_task.delay()
    logger.info(
        "cron.shopee-sync dispatched task_id=%s caller_ip=%s source=%s",
        task.id, caller_ip, request.headers.get("X-Cron-Source", "unknown"),
    )
    return {"status": "accepted", "task_id": task.id}


@router.get("/cron/health", status_code=status.HTTP_200_OK)
def cron_health(
    request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """Sanity check para o pg_cron validar conectividade sem enfileirar work."""
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(x_cron_secret, caller_ip)
    return {"status": "ok"}
