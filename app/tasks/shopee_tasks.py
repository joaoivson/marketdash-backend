import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Janela máxima de retentativas quando 0 conversões retornam
RETRY_INTERVAL_SECONDS = 3600          # 1 hora entre tentativas
MAX_RETRY_HOURS = 12                   # para de tentar após 12h (ex: 7h → 19h)
MAX_EMPTY_RETRIES = MAX_RETRY_HOURS    # 1 tentativa por hora


@celery_app.task(bind=True, max_retries=3, soft_time_limit=600, time_limit=700)
def sync_shopee_user_task(self, user_id: int, empty_attempt: int = 0):
    """
    Sincroniza comissões Shopee para um único usuário.

    Se retornar 0 conversões novas, reagenda para 1h depois (até MAX_EMPTY_RETRIES vezes).
    Se der exceção, usa o retry padrão do Celery (max 3x, 5 min de espera).
    """
    from app.db.session import SessionLocal
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
    from app.services.shopee_integration_service import ShopeeIntegrationService

    db = SessionLocal()
    try:
        svc = ShopeeIntegrationService(ShopeeIntegrationRepository(db))
        commissions = asyncio.run(svc.sync_user(user_id, db))

        if commissions == 0 and empty_attempt < MAX_EMPTY_RETRIES:
            next_attempt = empty_attempt + 1
            eta = datetime.now(timezone.utc) + timedelta(seconds=RETRY_INTERVAL_SECONDS)
            hours_elapsed = next_attempt
            hours_remaining = MAX_RETRY_HOURS - hours_elapsed
            logger.info(
                "Shopee sync user_id=%s: 0 conversões (tentativa %dh/%dh). "
                "Próxima tentativa às %s (%dh restantes).",
                user_id, hours_elapsed, MAX_RETRY_HOURS,
                eta.strftime("%H:%M UTC"), hours_remaining,
            )
            sync_shopee_user_task.apply_async(
                kwargs={"user_id": user_id, "empty_attempt": next_attempt},
                eta=eta,
            )
        elif commissions == 0:
            logger.warning(
                "Shopee sync user_id=%s: 0 conversões após %dh de tentativas. Encerrando.",
                user_id, MAX_RETRY_HOURS,
            )

        return {"status": "ok", "user_id": user_id, "commissions": commissions, "empty_attempt": empty_attempt}

    except Exception as exc:
        logger.error("sync_shopee_user_task falhou user_id=%s: %s", user_id, exc)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


@celery_app.task
def sync_all_shopee_users_task():
    """Disparado pelo beat às 7h. Itera todas as integrações ativas e agenda tarefas por usuário."""
    from app.db.session import SessionLocal
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository

    db = SessionLocal()
    try:
        repo = ShopeeIntegrationRepository(db)
        integrations = repo.get_all_active()
        for integ in integrations:
            sync_shopee_user_task.delay(integ.user_id, empty_attempt=0)
        logger.info("sync_all_shopee_users_task: %d tarefas agendadas", len(integrations))
        return {"dispatched": len(integrations)}
    finally:
        db.close()
