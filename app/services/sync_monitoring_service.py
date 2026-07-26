"""Observabilidade de execuções de sync (Shopee/Facebook) para o painel admin.

Separado de admin_metrics_service.py (que é billing/assinatura) — sync_runs é uma
preocupação operacional diferente.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.sync_run import SyncRun
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.repositories.sync_run_repository import SyncRunRepository

STALE_RUNNING_SECONDS = 3600  # running há mais de 1h = provavelmente morta (SIGKILL do time_limit)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _serialize_run(run: SyncRun) -> Dict[str, Any]:
    started = _as_utc(run.started_at)
    finished = _as_utc(run.finished_at)
    duration_seconds = (finished - started).total_seconds() if started and finished else None
    is_stale_running = (
        run.status == "running"
        and started is not None
        and (datetime.now(timezone.utc) - started).total_seconds() > STALE_RUNNING_SECONDS
    )
    return {
        "id": run.id,
        "source": run.source,
        "trigger": run.trigger,
        "user_id": run.user_id,
        "days_back": run.days_back,
        "empty_attempt": run.empty_attempt,
        "status": run.status,
        "started_at": started.isoformat() if started else None,
        "finished_at": finished.isoformat() if finished else None,
        "duration_seconds": duration_seconds,
        "records_fetched": run.records_fetched,
        "records_upserted": run.records_upserted,
        "is_suspected_partial": run.is_suspected_partial,
        "is_stale_running": is_stale_running,
        "error_message": run.error_message,
        "details": run.details,
    }


class SyncMonitoringService:
    def __init__(self, db: Session):
        self.db = db
        self.run_repo = SyncRunRepository(db)

    def list_runs(
        self,
        source: Optional[str] = None,
        trigger: Optional[str] = None,
        status: Optional[str] = None,
        user_id: Optional[int] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        runs = self.run_repo.list_by_filters(
            source=source, trigger=trigger, status=status, user_id=user_id, limit=limit,
        )
        return [_serialize_run(r) for r in runs]

    def full_sync_health(
        self,
        source: str = "shopee",
        trigger: str = "cron_full",
        since: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Cruza integrações ativas contra sync_runs pra responder "a madrugada completou
        pra todo mundo?" — hoje só dá pra inferir usuário por usuário via last_sync_at.
        """
        since = since or (datetime.now(timezone.utc) - timedelta(hours=24))

        if source == "shopee":
            active_user_ids = {i.user_id for i in ShopeeIntegrationRepository(self.db).get_all_active()}
        elif source == "facebook":
            active_user_ids = {i.user_id for i in FacebookIntegrationRepository(self.db).get_all_active()}
        else:
            active_user_ids = set()

        runs = self.run_repo.list_by_filters(source=source, trigger=trigger, since=since, limit=None)

        success_user_ids = {r.user_id for r in runs if r.status == "success" and r.user_id in active_user_ids}
        failed_user_ids = {
            r.user_id for r in runs if r.status == "failed" and r.user_id in active_user_ids
        } - success_user_ids
        missing_user_ids = active_user_ids - success_user_ids - failed_user_ids

        return {
            "source": source,
            "trigger": trigger,
            "since": since.isoformat(),
            "total_ativos": len(active_user_ids),
            "sucesso": len(success_user_ids),
            "falha": len(failed_user_ids),
            "sem_execucao": sorted(missing_user_ids),
        }

    def source_summary(self, source: str) -> Dict[str, Any]:
        """Card resumo por fonte pra tela Uso: última sync + status, chamadas/erros 24h."""
        since_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        runs_24h = self.run_repo.list_by_filters(source=source, since=since_24h, limit=None)
        latest = self.run_repo.list_by_filters(source=source, limit=1)
        last_run = latest[0] if latest else None
        return {
            "source": source,
            "last_sync_at": last_run.started_at.isoformat() if last_run and last_run.started_at else None,
            "last_status": last_run.status if last_run else None,
            "calls_24h": len(runs_24h),
            "errors_24h": sum(1 for r in runs_24h if r.status == "failed"),
        }

    def daily_call_counts(self, source: str, days: int = 30) -> List[Dict[str, Any]]:
        """Chamadas (execuções) e erros por dia, últimos N dias — pra gráfico de barras."""
        since = datetime.now(timezone.utc) - timedelta(days=days)
        runs = self.run_repo.list_by_filters(source=source, since=since, limit=None)
        buckets: Dict[str, Dict[str, int]] = {}
        for r in runs:
            if not r.started_at:
                continue
            day = r.started_at.date().isoformat()
            b = buckets.setdefault(day, {"calls": 0, "errors": 0})
            b["calls"] += 1
            if r.status == "failed":
                b["errors"] += 1
        return [{"date": d, **v} for d, v in sorted(buckets.items())]

    def latest_successful(self, source: str, user_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        if user_id is not None:
            run = self.run_repo.latest_for_user(user_id, source)
            return _serialize_run(run) if run and run.status == "success" else None
        runs = self.run_repo.list_by_filters(source=source, status="success", limit=1)
        return _serialize_run(runs[0]) if runs else None
