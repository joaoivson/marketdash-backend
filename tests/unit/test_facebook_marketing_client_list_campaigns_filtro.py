"""Rodada campanha-ativa: list_campaigns() passa a pedir ARCHIVED
explicitamente — sem isso, a Graph API omite campanhas arquivadas/
deletadas da resposta por padrão (doc oficial da Meta), e o sync nunca
mais atualiza o status de uma campanha depois que ela é arquivada."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.facebook_marketing_client import list_campaigns


def _fake_response(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


def _async_return(value):
    async def _inner():
        return value

    return _inner()


def _patch_httpx(body: dict, captured: dict):
    """Mocka o AsyncClient do httpx pra devolver `body` sem rede, capturando
    os kwargs da chamada em `captured` pra inspecionar depois."""
    client = MagicMock()

    async def _request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _fake_response(body)

    client.request = _request
    ctx = MagicMock()
    ctx.__aenter__ = lambda *a, **k: _async_return(client)
    ctx.__aexit__ = lambda *a, **k: _async_return(None)
    return patch("httpx.AsyncClient", return_value=ctx)


@pytest.mark.asyncio
async def test_list_campaigns_filtra_effective_status_incluindo_archived_sem_deleted():
    captured: dict = {}
    with _patch_httpx({"data": []}, captured):
        await list_campaigns("token123", "act_111")

    assert captured["params"] is not None
    assert "filtering" in captured["params"]
    filtros = json.loads(captured["params"]["filtering"])
    assert len(filtros) == 1
    assert filtros[0]["field"] == "effective_status"
    assert filtros[0]["operator"] == "IN"
    valores = filtros[0]["value"]
    assert "ARCHIVED" in valores
    assert "ACTIVE" in valores
    assert "PAUSED" in valores
    assert "DELETED" not in valores  # fora de escopo — campanha deletada, não arquivada
