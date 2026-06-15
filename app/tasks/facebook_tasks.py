import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, soft_time_limit=600, time_limit=700)
def sync_facebook_user_task(self, user_id: int):
    """Sincroniza campanhas + insights do Facebook para um único usuário."""
    from app.db.session import SessionLocal
    from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
    from app.services.facebook_integration_service import FacebookIntegrationService

    db = SessionLocal()
    try:
        svc = FacebookIntegrationService(FacebookIntegrationRepository(db))
        processed = asyncio.run(svc.sync_user(user_id, db))
        return {"status": "ok", "user_id": user_id, "campaigns": processed}
    except Exception as exc:
        logger.error("sync_facebook_user_task falhou user_id=%s: %s", user_id, exc)
        db.rollback()
        # exc pode ser HTTPException (não-picklável) → reempacota p/ o retry não quebrar.
        raise self.retry(exc=RuntimeError(str(exc)), countdown=300)
    finally:
        db.close()


@celery_app.task
def sync_all_facebook_users_task():
    """Itera integrações Facebook ativas e agenda uma task por usuário."""
    from app.db.session import SessionLocal
    from app.repositories.facebook_integration_repository import FacebookIntegrationRepository

    db = SessionLocal()
    try:
        repo = FacebookIntegrationRepository(db)
        integrations = repo.get_all_active()
        for integ in integrations:
            # priority=6: sync horário do FB fica acima da Shopee (9), abaixo do botão manual (0).
            sync_facebook_user_task.apply_async(args=[integ.user_id], priority=6)
        logger.info("sync_all_facebook_users_task: %d tarefas agendadas", len(integrations))
        return {"dispatched": len(integrations)}
    finally:
        db.close()
