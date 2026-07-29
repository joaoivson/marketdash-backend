"""Pausa de sync Shopee: cron não reenfileira contas quebradas até reconectar."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository
from app.services.shopee_graphql_client import ShopeePermanentError


def test_get_all_active_skips_paused_integrations():
    db = MagicMock()
    query = db.query.return_value
    filtered = query.filter.return_value
    filtered.order_by.return_value.all.return_value = []

    ShopeeIntegrationRepository(db).get_all_active()

    # filter recebeu is_active + sync_paused_at IS NULL
    assert query.filter.called
    call_args = query.filter.call_args
    # SQLAlchemy BinaryExpression — garante que passamos 2 condições
    assert len(call_args[0]) == 2


def test_upsert_clears_sync_pause():
    db = MagicMock()
    existing = SimpleNamespace(
        app_id="old",
        encrypted_password="x",
        is_active=True,
        sync_paused_at=datetime.now(timezone.utc),
        sync_pause_reason="never_synced_chronic_failure",
    )
    repo = ShopeeIntegrationRepository(db)
    repo.get_by_user_id = MagicMock(return_value=existing)

    out = repo.upsert(20, "18191340007", "enc")

    assert out.sync_paused_at is None
    assert out.sync_pause_reason is None
    assert out.app_id == "18191340007"
    db.flush.assert_called()


def test_pause_sync_is_idempotent():
    db = MagicMock()
    integ = SimpleNamespace(sync_paused_at=None, sync_pause_reason=None)
    repo = ShopeeIntegrationRepository(db)
    repo.get_by_user_id = MagicMock(return_value=integ)

    assert repo.pause_sync(20, "never_synced_chronic_failure") is True
    assert integ.sync_paused_at is not None
    assert integ.sync_pause_reason == "never_synced_chronic_failure"

    assert repo.pause_sync(20, "other") is False
    assert integ.sync_pause_reason == "never_synced_chronic_failure"


def test_task_pauses_on_permanent_error():
    from app.tasks.shopee_tasks import sync_shopee_user_task

    fake_db = MagicMock()
    fake_user = MagicMock(is_demo=False)
    fake_db.query.return_value.filter.return_value.first.return_value = fake_user

    pause_mock = MagicMock(return_value=True)

    with patch("app.db.session.SessionLocal", return_value=fake_db), patch.object(
        sync_shopee_user_task, "retry", MagicMock(side_effect=AssertionError("no retry"))
    ), patch(
        "app.services.shopee_integration_service.ShopeeIntegrationService.sync_user",
        side_effect=ShopeePermanentError("Credencial inválida", code=10020),
    ), patch(
        "app.repositories.shopee_integration_repository.ShopeeIntegrationRepository.pause_sync",
        pause_mock,
    ), patch(
        "app.repositories.shopee_integration_repository.ShopeeIntegrationRepository.get_by_user_id",
        return_value=SimpleNamespace(last_sync_at=None, sync_paused_at=None),
    ):
        result = sync_shopee_user_task.run(
            user_id=24, days_back=7, empty_attempt=0, trigger="cron_incremental"
        )

    assert result["status"] == "failed_permanent"
    pause_mock.assert_called()
    assert "permanent_error" in pause_mock.call_args[0][1]


def test_task_pauses_never_synced_system_error():
    from app.tasks.shopee_tasks import sync_shopee_user_task

    fake_db = MagicMock()
    fake_user = MagicMock(is_demo=False)
    fake_db.query.return_value.filter.return_value.first.return_value = fake_user

    pause_mock = MagicMock(return_value=True)

    with patch("app.db.session.SessionLocal", return_value=fake_db), patch.object(
        sync_shopee_user_task, "retry", MagicMock(side_effect=AssertionError("no retry"))
    ), patch(
        "app.services.shopee_integration_service.ShopeeIntegrationService.sync_user",
        side_effect=HTTPException(status_code=400, detail="System Error [10000]"),
    ), patch(
        "app.repositories.shopee_integration_repository.ShopeeIntegrationRepository.pause_sync",
        pause_mock,
    ), patch(
        "app.repositories.shopee_integration_repository.ShopeeIntegrationRepository.get_by_user_id",
        return_value=SimpleNamespace(last_sync_at=None, sync_paused_at=None),
    ):
        result = sync_shopee_user_task.run(
            user_id=20, days_back=7, empty_attempt=0, trigger="cron_incremental"
        )

    assert result["status"] == "failed_permanent"
    assert result["never_synced"] is True
    pause_mock.assert_called_once()
    assert pause_mock.call_args[0] == (20, "never_synced_chronic_failure")


@pytest.mark.asyncio
async def test_sync_commissions_rejects_non_numeric_app_id():
    from app.services.shopee_integration_service import ShopeeIntegrationService

    db = MagicMock()
    repo = MagicMock()
    repo.get_by_user_id.return_value = SimpleNamespace(
        is_active=True,
        app_id="machado.e.carine@gmail.com",
        encrypted_password="enc",
    )
    svc = ShopeeIntegrationService(repo)

    with pytest.raises(ShopeePermanentError) as exc_info:
        await svc.sync_commissions(24, db, days_back=7)

    assert exc_info.value.code == 10020
    assert "AppID" in str(exc_info.value)
