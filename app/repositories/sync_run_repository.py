from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.models.sync_run import SyncRun

# Um run "running" mais velho que isto está morto: o run mais longo que TERMINOU
# em 30 dias de produção levou 7,9 min, e o `time_limit` da task do Celery é 700s.
# 1h é ~8x a maior duração real — folga suficiente para nunca fechar run vivo.
STALE_RUNNING_SECONDS = 3600

# Não é "failed": nada falhou na API, o processo foi morto no meio. Marcar como
# falha inflaria `errors_24h` e a aba de erros do painel com ruído que não pede
# ação nenhuma.
STATUS_INTERROMPIDO = "interrupted"


class SyncRunRepository:
    def __init__(self, db: Session):
        self.db = db

    def fechar_orfaos(self, idade_segundos: int = STALE_RUNNING_SECONDS) -> int:
        """Fecha runs presos em 'running' porque o processo morreu no meio.

        O ciclo do cron roda como BackgroundTask no processo da API; um deploy,
        um restart ou um OOM mata o processo e a linha fica 'running' PARA
        SEMPRE — nada a marca. Em 28/08/2026 havia **50 delas** acumuladas
        desde 28/07, inflando "rodando agora" e escondendo o estado real.

        Usa o índice parcial `WHERE status = 'running'` (migration 037).
        Idempotente: rodar de novo não acha mais nada.
        """
        agora = datetime.now(timezone.utc)
        fechados = (
            self.db.query(SyncRun)
            .filter(
                SyncRun.status == "running",
                SyncRun.started_at < agora - timedelta(seconds=idade_segundos),
            )
            .update(
                {
                    "status": STATUS_INTERROMPIDO,
                    "finished_at": agora,
                    "error_message": (
                        "Processo interrompido antes de terminar (deploy, restart ou "
                        "timeout). Fechado automaticamente."
                    ),
                },
                synchronize_session=False,
            )
        )
        self.db.commit()
        return fechados

    def create(
        self,
        source: str,
        trigger: str,
        user_id: Optional[int] = None,
        days_back: Optional[int] = None,
        empty_attempt: int = 0,
    ) -> int:
        """Cria a linha em status='running' e retorna o id.

        Usa flush() (não commit) pra capturar o id gerado pela sequence ANTES do
        commit expirar os atributos do objeto (expire_on_commit=True) — mesmo
        cuidado já usado em shopee_integration_service.py com `ds_id`/`app_id`.
        """
        run = SyncRun(
            source=source,
            trigger=trigger,
            user_id=user_id,
            days_back=days_back,
            empty_attempt=empty_attempt,
            status="running",
        )
        self.db.add(run)
        self.db.flush()
        run_id = run.id
        self.db.commit()
        return run_id

    def mark_success(
        self,
        run_id: int,
        records_fetched: Optional[int] = None,
        records_upserted: Optional[int] = None,
        is_suspected_partial: bool = False,
        details: Optional[dict] = None,
    ) -> None:
        run = self.db.query(SyncRun).filter(SyncRun.id == run_id).first()
        if not run:
            return
        run.status = "success"
        run.finished_at = datetime.now(timezone.utc)
        run.records_fetched = records_fetched
        run.records_upserted = records_upserted
        run.is_suspected_partial = is_suspected_partial
        if details is not None:
            run.details = details
        self.db.commit()

    def mark_failed(self, run_id: int, error_message: str) -> None:
        run = self.db.query(SyncRun).filter(SyncRun.id == run_id).first()
        if not run:
            return
        run.status = "failed"
        run.finished_at = datetime.now(timezone.utc)
        run.error_message = (error_message or "")[:2000]
        self.db.commit()

    def mark_skipped_lock(self, run_id: int) -> None:
        run = self.db.query(SyncRun).filter(SyncRun.id == run_id).first()
        if not run:
            return
        run.status = "skipped_lock"
        run.finished_at = datetime.now(timezone.utc)
        self.db.commit()

    def list_by_filters(
        self,
        source: Optional[str] = None,
        trigger: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        since: Optional[datetime] = None,
        limit: Optional[int] = 100,
    ) -> List[SyncRun]:
        query = self.db.query(SyncRun)
        if source:
            query = query.filter(SyncRun.source == source)
        if trigger:
            query = query.filter(SyncRun.trigger == trigger)
        if status:
            query = query.filter(SyncRun.status == status)
        if user_id is not None:
            query = query.filter(SyncRun.user_id == user_id)
        if since:
            query = query.filter(SyncRun.started_at >= since)
        query = query.order_by(desc(SyncRun.started_at))
        if limit is not None:
            query = query.limit(limit)
        return query.all()

    def latest_for_user(self, user_id: int, source: str) -> Optional[SyncRun]:
        return (
            self.db.query(SyncRun)
            .filter(SyncRun.user_id == user_id, SyncRun.source == source)
            .order_by(desc(SyncRun.started_at))
            .first()
        )
