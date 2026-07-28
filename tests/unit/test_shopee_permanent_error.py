"""Credencial Shopee inválida (código 10020) é erro PERMANENTE: não pode virar
retry. Em produção (28/07/2026) duas contas nessa situação geravam ~106 falhas/dia
sozinhas — cada cron horário virava 1 tentativa + 3 retries, sem chance de sucesso."""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.shopee_graphql_client import (
    SHOPEE_PERMANENT_ERROR_CODES,
    ShopeePermanentError,
    execute_graphql,
)


def _fake_response(body: dict):
    resp = MagicMock()
    resp.json.return_value = body
    resp.raise_for_status.return_value = None
    return resp


def _patch_httpx(body: dict):
    """Mocka o AsyncClient do httpx pra devolver `body` sem rede."""
    client = MagicMock()

    async def _post(*args, **kwargs):
        return _fake_response(body)

    client.post = _post
    ctx = MagicMock()
    ctx.__aenter__ = lambda *a, **k: _async_return(client)
    ctx.__aexit__ = lambda *a, **k: _async_return(None)
    return patch("httpx.AsyncClient", return_value=ctx)


def _async_return(value):
    async def _inner():
        return value

    return _inner()


@pytest.mark.asyncio
async def test_invalid_credential_raises_permanent_error():
    body = {
        "errors": [
            {
                "message": "error [10020]: Invalid Credential",
                "extensions": {"code": 10020, "message": "Invalid Credential"},
            }
        ]
    }
    with _patch_httpx(body):
        with pytest.raises(ShopeePermanentError) as exc_info:
            await execute_graphql("app", "secret", "{ query }")
    assert exc_info.value.code == 10020
    assert "reconectar" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_system_error_stays_transient_http_exception():
    """10000 (System Error) é do lado da Shopee e costuma passar — continua retentável."""
    body = {
        "errors": [
            {
                "message": "error [10000]: System Error",
                "extensions": {"code": 10000, "message": "System Error"},
            }
        ]
    }
    with _patch_httpx(body):
        with pytest.raises(HTTPException) as exc_info:
            await execute_graphql("app", "secret", "{ query }")
    assert exc_info.value.status_code == 400
    assert not isinstance(exc_info.value, ShopeePermanentError)


def test_permanent_codes_are_narrow():
    """Guarda contra alguém alargar demais a lista: só credencial é permanente hoje.
    Erro transitório marcado como permanente pararia de tentar de vez."""
    assert SHOPEE_PERMANENT_ERROR_CODES == {10020}


def test_task_does_not_retry_permanent_error():
    """A task registra o erro e ENCERRA — sem self.retry()."""
    from app.tasks.shopee_tasks import sync_shopee_user_task

    fake_db = MagicMock()
    fake_user = MagicMock(is_demo=False)
    fake_db.query.return_value.filter.return_value.first.return_value = fake_user

    retry_mock = MagicMock(side_effect=AssertionError("não pode retentar erro permanente"))

    with patch("app.db.session.SessionLocal", return_value=fake_db), patch.object(
        sync_shopee_user_task, "retry", retry_mock
    ), patch(
        "app.services.shopee_integration_service.ShopeeIntegrationService.sync_user",
        side_effect=ShopeePermanentError("Credencial Shopee inválida (código 10020).", code=10020),
    ):
        result = sync_shopee_user_task.run(
            user_id=24, days_back=7, empty_attempt=0, trigger="cron_incremental"
        )

    assert result["status"] == "failed_permanent"
    retry_mock.assert_not_called()


def test_task_still_retries_transient_error():
    """Contraprova: erro transitório continua reagendando (não quebrei o retry normal)."""
    from app.tasks.shopee_tasks import sync_shopee_user_task

    fake_db = MagicMock()
    fake_user = MagicMock(is_demo=False)
    fake_db.query.return_value.filter.return_value.first.return_value = fake_user

    retry_mock = MagicMock(return_value=RuntimeError("retry agendado"))

    with patch("app.db.session.SessionLocal", return_value=fake_db), patch.object(
        sync_shopee_user_task, "retry", retry_mock
    ), patch(
        "app.services.shopee_integration_service.ShopeeIntegrationService.sync_user",
        side_effect=HTTPException(status_code=400, detail="System Error"),
    ):
        with pytest.raises(RuntimeError):
            sync_shopee_user_task.run(
                user_id=20, days_back=7, empty_attempt=0, trigger="cron_incremental"
            )

    retry_mock.assert_called_once()
