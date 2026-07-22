import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

# Janela máxima de retentativas quando 0 conversões retornam
RETRY_INTERVAL_SECONDS = 3600          # 1 hora entre tentativas
MAX_RETRY_HOURS = 12                   # para de tentar após 12h (ex: 7h → 19h)
MAX_EMPTY_RETRIES = MAX_RETRY_HOURS    # 1 tentativa por hora


# O backfill de 88 dias de contas grandes (milhares de pedidos, dezenas de páginas por
# chunk) pode passar de 10 min. Com o limite antigo (600s) a task estourava o
# soft_time_limit, era MORTA e re-tentava do ZERO (DELETE+reinsert) num LOOP que nunca
# concluía (a sincronização ficava "girando" pra sempre). Limite generoso (50 min) pra o
# backfill completar de uma vez; tasks normais (incrementais) terminam em segundos.
@celery_app.task(bind=True, max_retries=3, soft_time_limit=3000, time_limit=3300)
def sync_shopee_user_task(self, user_id: int, days_back: int = 88, empty_attempt: int = 0):
    """
    Sincroniza comissões Shopee para um único usuário.
    """
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
    from app.services.shopee_integration_service import ShopeeIntegrationService

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user and getattr(user, "is_demo", False):
            logger.info("Shopee sync pulado user_id=%s (is_demo)", user_id)
            return {"status": "skipped", "user_id": user_id, "reason": "is_demo"}

        svc = ShopeeIntegrationService(ShopeeIntegrationRepository(db))
        commissions = asyncio.run(svc.sync_user(user_id, db, days_back=days_back))

        if commissions == 0 and days_back <= 7:
            logger.info(
                "Shopee sync user_id=%s: 0 conversões em %d dias (janela curta/cron, sem retry).",
                user_id, days_back,
            )
        elif commissions == 0 and empty_attempt < MAX_EMPTY_RETRIES:
            next_attempt = empty_attempt + 1
            eta = datetime.now(timezone.utc) + timedelta(seconds=RETRY_INTERVAL_SECONDS)
            hours_elapsed = next_attempt
            hours_remaining = MAX_RETRY_HOURS - hours_elapsed
            logger.info(
                "Shopee sync user_id=%s: 0 conversões em %d dias (tentativa %dh/%dh). "
                "Próxima tentativa às %s (%dh restantes).",
                user_id, days_back, hours_elapsed, MAX_RETRY_HOURS,
                eta.strftime("%H:%M UTC"), hours_remaining,
            )
            sync_shopee_user_task.apply_async(
                kwargs={"user_id": user_id, "days_back": days_back, "empty_attempt": next_attempt},
                eta=eta,
                priority=9,
            )
        elif commissions == 0:
            logger.warning(
                "Shopee sync user_id=%s: 0 conversões em %d dias após %dh de tentativas. Encerrando.",
                user_id, days_back, MAX_RETRY_HOURS,
            )

        return {"status": "ok", "user_id": user_id, "commissions": commissions, "empty_attempt": empty_attempt, "days_back": days_back}

    except Exception as exc:
        logger.error("sync_shopee_user_task falhou user_id=%s: %s", user_id, exc)
        raise self.retry(exc=RuntimeError(str(exc)), countdown=300)
    finally:
        db.close()


@celery_app.task
def sync_all_shopee_users_task(days_back: int = 7):
    """
    Fan-out Celery por usuário (pula is_demo). O cron horário usa path INLINE
    (run_shopee_sync_all); esta task permanece para uso manual/legado.
    """
    from app.db.session import SessionLocal
    from app.models.user import User
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository

    db = SessionLocal()
    try:
        repo = ShopeeIntegrationRepository(db)
        integrations = repo.get_all_active()
        dispatched = 0
        for integ in integrations:
            user = db.query(User).filter(User.id == integ.user_id).first()
            if user and getattr(user, "is_demo", False):
                continue
            sync_shopee_user_task.apply_async(
                kwargs={"user_id": integ.user_id, "days_back": days_back, "empty_attempt": 0},
                priority=9,
            )
            dispatched += 1
        logger.info("sync_all_shopee_users_task: %d tarefas agendadas (days_back=%d)", dispatched, days_back)
        return {"dispatched": dispatched, "days_back": days_back}
    finally:
        db.close()
