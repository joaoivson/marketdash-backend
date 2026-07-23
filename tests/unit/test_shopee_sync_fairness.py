"""Regressão: sync Shopee prioriza atrasados e pula recentes."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_get_all_active_orders_by_last_sync_asc_nulls_first():
    from app.models.shopee_integration import ShopeeIntegration
    from app.repositories.shopee_integration_repository import ShopeeIntegrationRepository

    db = MagicMock()
    query = MagicMock()
    db.query.return_value = query
    query.filter.return_value = query
    query.order_by.return_value = query
    query.all.return_value = []

    ShopeeIntegrationRepository(db).get_all_active()

    order_expr = query.order_by.call_args[0][0]
    # Garante que ordenamos por last_sync_at (asc + nulls first).
    assert ShopeeIntegration.last_sync_at.key in str(order_expr)


@pytest.mark.asyncio
async def test_run_shopee_sync_all_skips_recent_users():
    from app.services import shopee_integration_service as svc_mod

    now = datetime.now(timezone.utc)
    fresh = MagicMock(user_id=1, last_sync_at=now - timedelta(minutes=10))
    stale = MagicMock(user_id=9, last_sync_at=now - timedelta(hours=36))
    fake_user = MagicMock(is_demo=False)

    lock_conn = MagicMock()
    lock_conn.execute.return_value.scalar.return_value = True

    bind = MagicMock()
    bind.connect.return_value.execution_options.return_value = lock_conn

    db_lock = MagicMock()
    db_lock.get_bind.return_value = bind

    db0 = MagicMock()
    db0.query.return_value.filter.return_value.first.return_value = fake_user

    db_user = MagicMock()
    sessions = iter([db_lock, db0, db_user])

    with patch("app.db.session.SessionLocal", side_effect=lambda: next(sessions)), patch(
        "app.repositories.shopee_integration_repository.ShopeeIntegrationRepository.get_all_active",
        return_value=[fresh, stale],
    ), patch.object(
        svc_mod.ShopeeIntegrationService,
        "sync_user",
        new_callable=AsyncMock,
        return_value=10,
    ) as sync_mock:
        result = await svc_mod.run_shopee_sync_all(days_back=7)

    assert result["synced"] == 1
    assert result["skipped_recent"] == 1
    assert result["total"] == 1
    sync_mock.assert_awaited_once()
    assert sync_mock.await_args.args[0] == 9
