"""
Endpoints internos chamados por scheduler externo (pg_cron + pg_net no Supabase).

Autenticação por secret compartilhado. Aceita ambos os formatos para compatibilidade:
  - Authorization: Bearer <CRON_SECRET>   (preferencial — passa por qualquer proxy)
  - X-Cron-Secret: <CRON_SECRET>          (legado, pode ser strippeado por WAFs)
"""
import hmac
import logging

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request, status

from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["internal"])


def _extract_secret(authorization: str | None, x_cron_secret: str | None) -> str | None:
    """Retorna o secret recebido em qualquer um dos dois headers aceitos."""
    if authorization:
        token = authorization.strip()
        if token.lower().startswith("bearer "):
            return token[7:].strip()
        return token
    return x_cron_secret


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
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Disparado pelo pg_cron via pg_net com tipo: full (90d madrugada) ou incremental (3d horário).

    Query: ?type=full|incremental (padrão: incremental)
    Enfileira sync_all_shopee_users_task no Celery worker e retorna imediatamente.
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)

    from app.tasks.shopee_tasks import sync_all_shopee_users_task

    sync_type = request.query_params.get("type", "incremental")
    days_back = 90 if sync_type == "full" else 3

    task = sync_all_shopee_users_task.delay(days_back=days_back)
    logger.info(
        "cron.shopee-sync dispatched task_id=%s type=%s days_back=%d caller_ip=%s source=%s",
        task.id, sync_type, days_back, caller_ip, request.headers.get("X-Cron-Source", "unknown"),
    )
    return {"status": "accepted", "task_id": task.id, "sync_type": sync_type, "days_back": days_back}


@router.post("/cron/facebook-sync", status_code=status.HTTP_202_ACCEPTED)
async def cron_facebook_sync(
    request: Request,
    background_tasks: BackgroundTasks,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """
    Disparado pelo pg_cron via pg_net (de hora em hora).

    Roda o sync de TODOS os usuários Facebook INLINE, num BackgroundTask do FastAPI —
    SEM Celery/worker. Retorna 202 na hora (satisfaz o timeout do pg_net) e o sync
    continua no próprio processo da API.
    """
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)

    from app.services.facebook_integration_service import run_facebook_sync_all

    background_tasks.add_task(run_facebook_sync_all)
    logger.info(
        "cron.facebook-sync (inline/background, sem worker) caller_ip=%s source=%s",
        caller_ip, request.headers.get("X-Cron-Source", "unknown"),
    )
    return {"status": "accepted", "mode": "background-inline"}


@router.get("/cron/health", status_code=status.HTTP_200_OK)
def cron_health(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
):
    """Sanity check para o pg_cron validar conectividade sem enfileirar work."""
    caller_ip = request.client.host if request.client else "unknown"
    _validate_cron_secret(_extract_secret(authorization, x_cron_secret), caller_ip)
    return {"status": "ok"}
