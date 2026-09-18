"""Descarga em lote do buffer de cliques (ver app/services/click_buffer.py).

Não há Celery beat neste projeto (agendamento é via pg_cron), então a task se
autoagenda: o primeiro clique de cada janela a enfileira com countdown, e ao
terminar ela se reagenda se ainda houver eventos pendentes.
"""
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=5, soft_time_limit=90, time_limit=120)
def descarregar_cliques(self):
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services import click_buffer

    db = SessionLocal()
    try:
        resultado = click_buffer.descarregar(db)
    except Exception as exc:
        # O lote já voltou ao Redis; tenta de novo com backoff (banco lento).
        logger.warning("Descarga de cliques falhou, retry: %s", exc)
        raise self.retry(exc=exc, countdown=min(60 * (self.request.retries + 1), 300))
    finally:
        db.close()

    if resultado.links_atualizados or resultado.eventos_gravados:
        logger.info(
            "Cliques descarregados: %d link(s), %d evento(s), %d restante(s)",
            resultado.links_atualizados, resultado.eventos_gravados, resultado.restantes,
        )
    if resultado.restantes:
        descarregar_cliques.apply_async(countdown=settings.CLIQUES_FLUSH_INTERVALO_S)
    return {
        "links": resultado.links_atualizados,
        "eventos": resultado.eventos_gravados,
        "restantes": resultado.restantes,
    }
