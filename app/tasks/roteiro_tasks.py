"""
Task do motor (F3): processa UMA fatia e se re-agenda se sobrou trabalho.

Sempre priority=9 (batch). NUNCA 0..8 intermediários: só as pontas das filas
são consumidas — priority=5 é aceita e nunca executa, em silêncio (foi o
incidente do sync manual). `task_acks_late` + claim atômico tornam a
re-execução segura: linha presa vira `falhou` e nunca é reenviada.
"""
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="roteiros.processar_execucao", bind=True)
def processar_execucao(self, execucao_id: int) -> dict:
    from app.db.session import SessionLocal
    from app.repositories.sync_run_repository import SyncRunRepository
    from app.services.roteiro_envio_service import RoteiroEnvioService

    db = SessionLocal()
    try:
        sync_repo = SyncRunRepository(db)
        run = sync_repo.create(source="roteiro_execucao", trigger="cron",
                               user_id=None)
        try:
            resultado = RoteiroEnvioService(db).processar_fatia(execucao_id)
        except Exception as e:
            db.rollback()
            sync_repo.mark_failed(run, error_message=str(e)[:500])
            logger.exception("Fatia da execução %s falhou", execucao_id)
            return {"erro": "falha inesperada"}
        sync_repo.mark_success(run, records_fetched=resultado.get("enviadas", 0),
                               records_upserted=resultado.get("enviadas", 0))
        if resultado.get("reagendar"):
            # Sobrou pendente e o orçamento da fatia acabou: a PRÓPRIA task se
            # re-agenda — nunca um loop no processo (morreria no deploy).
            processar_execucao.apply_async(args=[execucao_id], countdown=5,
                                           priority=9)
        logger.info("Execução %s: fatia %s", execucao_id, resultado)
        return resultado
    finally:
        db.close()
