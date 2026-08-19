import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, soft_time_limit=120, time_limit=150)
def processar_comentario_instagram_task(self, ig_user_id: str, valor: dict):
    """Processa UM comentário: matching, dedupe, direct e resposta pública.

    Política de retry (spec §6.3): só falha de rede e 5xx, no máximo 3 vezes.
    Erro de negócio — já respondido (2534014), janela expirada, permissão ausente
    — NÃO é retentado: a API rejeita de novo, gasta cota e conta contra a
    reputação do app. O pipeline já registra o motivo em instagram_events.
    """
    from app.db.session import SessionLocal
    from app.repositories.instagram_automation_repository import InstagramAutomationRepository
    from app.services import instagram_login_client as ig
    from app.services.instagram_comment_pipeline import (
        InstagramCommentPipeline,
        ThrottleExcedido,
    )

    db = SessionLocal()
    try:
        pipeline = InstagramCommentPipeline(InstagramAutomationRepository(db))
        resultado = asyncio.run(pipeline.processar_comentario(ig_user_id, valor or {}))
        logger.info(
            "Instagram comentário %s ig_user_id=%s -> %s",
            (valor or {}).get("id"), ig_user_id, resultado,
        )
        return resultado
    except ThrottleExcedido as exc:
        db.rollback()
        # Teto horário: não é falha, é espera. Não consome tentativa de retry.
        raise self.retry(exc=exc, countdown=exc.segundos_para_tentar, max_retries=10)
    except ig.InstagramApiError as exc:
        db.rollback()
        if exc.permanente:
            logger.info(
                "Instagram: erro permanente comment=%s (%s) — sem retry",
                (valor or {}).get("id"), exc.codigo_curto,
            )
            return {"status": "falhou", "permanente": True, "erro": exc.mensagem}
        # Backoff exponencial: 60s, 120s, 240s.
        espera = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=espera)
    except Exception as exc:
        db.rollback()
        logger.error(
            "processar_comentario_instagram_task falhou comment=%s: %s",
            (valor or {}).get("id"), exc,
        )
        raise self.retry(exc=RuntimeError(str(exc)), countdown=120)
    finally:
        db.close()


@celery_app.task
def renovar_tokens_instagram_task():
    """Renova os tokens que vencem em menos de 10 dias.

    Existe além do cron do pg_cron para o caso de o ambiente rodar com Celery
    beat em vez de pg_net — as duas rotas chamam a mesma função e a renovação é
    idempotente (renovar antes da hora só estende a validade).
    """
    from app.services.instagram_connection_service import run_instagram_token_refresh_all

    return asyncio.run(run_instagram_token_refresh_all())
