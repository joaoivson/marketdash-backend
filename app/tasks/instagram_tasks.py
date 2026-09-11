import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3, soft_time_limit=120, time_limit=150)
def processar_comentario_instagram_task(
    self, ig_user_id: str, valor: dict, entrega_id: int | None = None
):
    """Processa UM comentário: matching, dedupe, direct e resposta pública.

    Política de retry (spec §6.3): só falha de rede e 5xx, no máximo 3 vezes.
    Erro de negócio — já respondido (2534014), janela expirada, permissão ausente
    — NÃO é retentado: a API rejeita de novo, gasta cota e conta contra a
    reputação do app. O pipeline já registra o motivo em instagram_events.

    `entrega_id` aponta a linha do ledger de entrega (migration 083). Todo
    caminho de saída daqui carimba o desfecho nela, inclusive os que o pipeline
    descarta sem gravar em instagram_events — "nenhuma automação cobre este
    post" era o descarte que não deixava rastro nenhum.
    """
    from app.api.webhooks.instagram import marcar_desfecho_entrega
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
        marcar_desfecho_entrega(
            entrega_id,
            (resultado or {}).get("status") or "desconhecido",
            (resultado or {}).get("motivo") or (resultado or {}).get("erro"),
        )
        return resultado
    except ThrottleExcedido as exc:
        db.rollback()
        marcar_desfecho_entrega(entrega_id, "adiado_throttle", str(exc))
        # Teto horário: não é falha, é espera. Não consome tentativa de retry.
        raise self.retry(exc=exc, countdown=exc.segundos_para_tentar, max_retries=10)
    except ig.InstagramApiError as exc:
        db.rollback()
        if exc.permanente:
            logger.info(
                "Instagram: erro permanente comment=%s (%s) — sem retry",
                (valor or {}).get("id"), exc.codigo_curto,
            )
            marcar_desfecho_entrega(entrega_id, "falhou", f"{exc.codigo_curto}: {exc.mensagem}")
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
        marcar_desfecho_entrega(entrega_id, "erro_retry", str(exc))
        raise self.retry(exc=RuntimeError(str(exc)), countdown=120)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, soft_time_limit=120, time_limit=150)
def processar_story_reply_instagram_task(
    self, ig_user_id: str, evento: dict, entrega_id: int | None = None
):
    """Processa UM reply de story: matching, dedupe e a DM de resposta.

    Mesma política de retry da task de comentário: só rede/5xx, com o throttle
    horário reenfileirando sem queimar tentativa. `entrega_id` carimba o
    desfecho no ledger (migration 083), pelos mesmos motivos.
    """
    from app.api.webhooks.instagram import marcar_desfecho_entrega
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
        resultado = asyncio.run(pipeline.processar_story_reply(ig_user_id, evento or {}))
        logger.info(
            "Instagram story reply %s ig_user_id=%s -> %s",
            (evento or {}).get("mid"), ig_user_id, resultado,
        )
        marcar_desfecho_entrega(
            entrega_id,
            (resultado or {}).get("status") or "desconhecido",
            (resultado or {}).get("motivo") or (resultado or {}).get("erro"),
        )
        return resultado
    except ThrottleExcedido as exc:
        db.rollback()
        marcar_desfecho_entrega(entrega_id, "adiado_throttle", str(exc))
        raise self.retry(exc=exc, countdown=exc.segundos_para_tentar, max_retries=10)
    except ig.InstagramApiError as exc:
        db.rollback()
        if exc.permanente:
            logger.info(
                "Instagram: erro permanente story mid=%s (%s) — sem retry",
                (evento or {}).get("mid"), exc.codigo_curto,
            )
            marcar_desfecho_entrega(entrega_id, "falhou", f"{exc.codigo_curto}: {exc.mensagem}")
            return {"status": "falhou", "permanente": True, "erro": exc.mensagem}
        espera = 60 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=espera)
    except Exception as exc:
        db.rollback()
        logger.error(
            "processar_story_reply_instagram_task falhou mid=%s: %s",
            (evento or {}).get("mid"), exc,
        )
        marcar_desfecho_entrega(entrega_id, "erro_retry", str(exc))
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
