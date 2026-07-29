"""daily_call_counts deve agrupar por dia civil em America/Sao_Paulo, não UTC."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.sync_monitoring_service import SyncMonitoringService


def test_daily_call_counts_buckets_by_brazil_calendar_day():
    # 28/07/2026 22:00:52 BRT = 29/07/2026 01:00:52 UTC
    started_utc = datetime(2026, 7, 29, 1, 0, 52, tzinfo=timezone.utc)
    run = SimpleNamespace(started_at=started_utc, status="failed")

    db = MagicMock()
    svc = SyncMonitoringService(db)
    svc.run_repo = MagicMock()
    svc.run_repo.list_by_filters.return_value = [run]

    points = svc.daily_call_counts("shopee", days=30)

    assert points == [{"date": "2026-07-28", "calls": 1, "errors": 1}]
