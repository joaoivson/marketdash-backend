import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.sync_gate import sync_liberado_para
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
def sync_shopee_user_task(self, user_id: int, days_back: int = 88, empty_attempt: int = 0, trigger: str = "manual"):
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
        commissions = asyncio.run(
            svc.sync_user(user_id, db, days_back=days_back, trigger=trigger, empty_attempt=empty_attempt)
        )

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
                kwargs={
                    "user_id": user_id,
                    "days_back": days_back,
                    "empty_attempt": next_attempt,
                    "trigger": "empty_retry",
                },
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
        from app.services.shopee_graphql_client import ShopeePermanentError

        is_permanent = isinstance(exc, ShopeePermanentError)

        # Conta que NUNCA sincronizou com sucesso (last_sync_at nulo) e falha = credencial
        # mal configurada desde o começo, não instabilidade. A Shopee às vezes devolve
        # "System Error" (10000) genérico nesse caso em vez de "Invalid Credential", então
        # não dá pra decidir só pelo código do erro. Retentar 3x de 5 em 5 min não tem
        # chance de sucesso e é o que sobrou enchendo o painel. Contas saudáveis (com
        # last_sync_at preenchido) mantêm o retry normal — falha ali é transitória de verdade.
        never_synced = False
        pause_reason = None
        try:
            # Após sync_user fazer rollback, garante sessão limpa antes de ler a integração.
            try:
                db.rollback()
            except Exception:
                pass
            from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository

            repo = ShopeeIntegrationRepository(db)
            integ = repo.get_by_user_id(user_id)
            never_synced = bool(integ and integ.last_sync_at is None)
            if is_permanent:
                pause_reason = f"permanent_error:{getattr(exc, 'code', None) or 'unknown'}"
            elif never_synced:
                pause_reason = "never_synced_chronic_failure"
        except Exception as probe_exc:
            logger.warning(
                "sync_shopee_user_task user_id=%s: falha ao inspecionar integração (%s)",
                user_id,
                probe_exc,
            )

        skip_retry = is_permanent or never_synced
        if is_permanent:
            logger.warning(
                "sync_shopee_user_task user_id=%s: credencial inválida — sem retry (%s)",
                user_id, exc,
            )
        elif never_synced:
            logger.warning(
                "sync_shopee_user_task user_id=%s: conta nunca sincronizou (credencial "
                "provavelmente incorreta) — sem retry (%s)",
                user_id, exc,
            )
        else:
            logger.error("sync_shopee_user_task falhou user_id=%s: %s", user_id, exc)
        try:
            from app.models.sync_error_log import SyncErrorLog
            from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository

            db.add(SyncErrorLog(user_id=user_id, source="shopee", error_message=str(exc)[:2000]))
            if pause_reason:
                # Para o cron horário de reenfileirar esta conta até o usuário reconectar.
                ShopeeIntegrationRepository(db).pause_sync(user_id, pause_reason)
            db.commit()
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
        if skip_retry:
            # Não reagenda: só reconectando a conta resolve. Retentar geraria 4x a carga
            # e enche o painel de erro repetido sem nenhuma chance de sucesso.
            return {
                "status": "failed_permanent",
                "user_id": user_id,
                "reason": str(exc),
                "never_synced": never_synced,
            }
        raise self.retry(exc=RuntimeError(str(exc)), countdown=300)
    finally:
        db.close()


@celery_app.task
def sync_all_shopee_users_task(days_back: int = 7, trigger: str = "cron_incremental"):
    """
    Fan-out Celery por usuário (pula is_demo). Usado pelo cron horário
    (com fallback inline se o broker falhar). Prioriza last_sync_at antigo e
    pula quem sincronizou nos últimos SKIP_RECENT_SYNC_MINUTES.
    """
    from datetime import datetime, timedelta, timezone

    from app.db.session import SessionLocal
    from app.models.user import User
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
    from app.services.shopee_integration_service import SKIP_RECENT_SYNC_MINUTES

    db = SessionLocal()
    try:
        repo = ShopeeIntegrationRepository(db)
        integrations = repo.get_all_active()
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=SKIP_RECENT_SYNC_MINUTES)
        dispatched = 0
        skipped_recent = 0
        skipped_gate = 0
        for integ in integrations:
            user = db.query(User).filter(User.id == integ.user_id).first()
            if user and getattr(user, "is_demo", False):
                continue
            # Mesmo gate do caminho inline (app/core/sync_gate.py): em
            # homologação só a conta liberada sincroniza.
            if not sync_liberado_para(user.email if user else None):
                skipped_gate += 1
                continue
            last = integ.last_sync_at
            if last is not None:
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                if last >= cutoff:
                    skipped_recent += 1
                    continue
            sync_shopee_user_task.apply_async(
                kwargs={
                    "user_id": integ.user_id,
                    "days_back": days_back,
                    "empty_attempt": 0,
                    "trigger": trigger,
                },
                priority=9,
            )
            dispatched += 1
        logger.info(
            "sync_all_shopee_users_task: %d tarefas agendadas "
            "(days_back=%d skipped_recent=%d skipped_gate=%d)",
            dispatched, days_back, skipped_recent, skipped_gate,
        )
        return {
            "dispatched": dispatched,
            "days_back": days_back,
            "skipped_recent": skipped_recent,
        }
    finally:
        db.close()
