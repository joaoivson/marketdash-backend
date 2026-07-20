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

    Args:
        user_id: ID do usuário
        days_back: Número de dias a sincronizar (padrão: 88 = ~3 meses)
                   88 = sync incremental (padrão)
                   90 = reconcile completo
        empty_attempt: Contador de tentativas quando retorna 0 conversões (interno)

    Se retornar 0 conversões novas, reagenda para 1h depois (até MAX_EMPTY_RETRIES vezes).
    Se der exceção, usa o retry padrão do Celery (max 3x, 5 min de espera).
    """
    from app.db.session import SessionLocal
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
    from app.services.shopee_integration_service import ShopeeIntegrationService

    db = SessionLocal()
    try:
        svc = ShopeeIntegrationService(ShopeeIntegrationRepository(db))
        commissions = asyncio.run(svc.sync_user(user_id, db, days_back=days_back))

        if commissions == 0 and days_back <= 3:
            # Sync incremental (cron horário): SEM retry chain — o próximo disparo
            # do cron já cobre o atraso da Shopee. Reagendar aqui empilhava até 12
            # tasks/hora por usuário sem vendas e derrubou o banco (incidente 20/07).
            logger.info(
                "Shopee sync user_id=%s: 0 conversões em %d dias (incremental, sem retry).",
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
        # exc pode ser HTTPException (erro transitório da Shopee), que NÃO é picklável e
        # quebra o retry (UnpickleableExceptionWrapper). Reempacota num erro picklável.
        raise self.retry(exc=RuntimeError(str(exc)), countdown=300)
    finally:
        db.close()


@celery_app.task
def sync_all_shopee_users_task(days_back: int = 3):
    """
    Disparado pelo pg_cron. Itera todas as integrações ativas e agenda tarefas por usuário.

    Args:
        days_back: Número de dias a sincronizar
                   3 = incremental (horário, cron 9-12h)
                   90 = full reconcile (madrugada, cron 01h)
    """
    from app.db.session import SessionLocal
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository

    db = SessionLocal()
    try:
        repo = ShopeeIntegrationRepository(db)
        integrations = repo.get_all_active()
        for integ in integrations:
            sync_shopee_user_task.apply_async(
                kwargs={"user_id": integ.user_id, "days_back": days_back, "empty_attempt": 0},
                priority=9,
            )
        logger.info("sync_all_shopee_users_task: %d tarefas agendadas (days_back=%d)", len(integrations), days_back)
        return {"dispatched": len(integrations), "days_back": days_back}
    finally:
        db.close()
