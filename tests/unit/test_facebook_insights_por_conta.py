"""Insights do Meta passam a vir em UMA chamada por conta, não uma por campanha.

Incidente de 26/08/2026: com 70+ campanhas numa conta e o cron de hora em hora, o
sync disparava mais de 2.000 chamadas/hora a `{campaign_id}/insights`. A Graph API
passou a responder `data: []` — HTTP 200, sem erro — para TODAS as campanhas de 3
contas, permanentemente, enquanto a chamada de nível-conta continuava respondendo.
O gasto sumiu da tela dessas alunas por dois dias e o painel marcava "success 72/72",
porque o contador do sync contava CAMPANHAS LISTADAS, não insights gravados.
"""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.facebook_integration_service import (
    agrupar_insights_por_campanha,
    insight_vazio_com_entrega,
)
from app.services.facebook_marketing_client import get_account_campaign_insights


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


class TestChamadaDeNivelConta:
    @pytest.mark.asyncio
    async def test_bate_na_conta_e_nao_na_campanha(self):
        """Uma chamada por CONTA: o custo em rate limit não cresce com o nº de campanhas."""
        captured: dict = {}
        with _patch_httpx({"data": []}, captured):
            await get_account_campaign_insights("token123", "act_111", "2026-08-25", "2026-08-28")

        assert captured["url"].endswith("/act_111/insights")
        assert "/insights" in captured["url"]
        # Não pode ser a URL de uma campanha específica (o N+1 que estrangulava a conta).
        assert "act_111" in captured["url"]

    @pytest.mark.asyncio
    async def test_pede_campaign_id_para_dar_pra_agrupar_depois(self):
        captured: dict = {}
        with _patch_httpx({"data": []}, captured):
            await get_account_campaign_insights("token123", "act_111", "2026-08-25", "2026-08-28")

        params = captured["params"]
        assert "campaign_id" in params["fields"]
        assert params["level"] == "campaign"
        assert params["time_increment"] == 1
        assert json.loads(params["time_range"]) == {"since": "2026-08-25", "until": "2026-08-28"}
        # Sem breakdown: esta é a linha TOTAL do dia, não o recorte por placement.
        assert "breakdowns" not in params

    @pytest.mark.asyncio
    async def test_continua_pedindo_metricas_de_clique_no_link(self):
        """inline_link_* é o que o Gerenciador mostra e o que importa pro afiliado."""
        captured: dict = {}
        with _patch_httpx({"data": []}, captured):
            await get_account_campaign_insights("token123", "act_111", "2026-08-25", "2026-08-28")

        fields = captured["params"]["fields"]
        for campo in ("spend", "inline_link_clicks", "impressions", "reach", "actions", "date_start"):
            assert campo in fields


class TestAgruparInsightsPorCampanha:
    def test_distribui_cada_dia_para_a_campanha_certa(self):
        resposta = [
            {"campaign_id": "111", "date_start": "2026-08-27", "spend": "11.27"},
            {"campaign_id": "222", "date_start": "2026-08-27", "spend": "10.88"},
            {"campaign_id": "111", "date_start": "2026-08-28", "spend": "3.92"},
        ]
        agrupado = agrupar_insights_por_campanha(resposta)

        assert set(agrupado) == {"111", "222"}
        assert len(agrupado["111"]) == 2
        assert [d["date_start"] for d in agrupado["111"]] == ["2026-08-27", "2026-08-28"]
        assert agrupado["222"][0]["spend"] == "10.88"

    def test_campaign_id_numerico_vira_string_igual_a_do_banco(self):
        """fb_campaign_id é String(64) no banco; a Graph API às vezes manda número."""
        agrupado = agrupar_insights_por_campanha([{"campaign_id": 111, "spend": "1.00"}])
        assert "111" in agrupado

    def test_linha_sem_campaign_id_e_descartada(self):
        agrupado = agrupar_insights_por_campanha(
            [{"spend": "5.00"}, {"campaign_id": "", "spend": "6.00"}, {"campaign_id": None}]
        )
        assert agrupado == {}

    def test_resposta_vazia_nao_quebra(self):
        assert agrupar_insights_por_campanha([]) == {}


class TestGuardaDeInsightVazio:
    def test_acusa_quando_a_conta_entregou_mas_nenhum_insight_entrou(self):
        """O caso da Alice: placement trouxe o gasto de ontem, insight não trouxe nada."""
        assert insight_vazio_com_entrega(insights_upserted=0, placement_upserted=8) is True

    def test_nao_acusa_quem_simplesmente_parou_de_anunciar(self):
        """Sem entrega no período as DUAS chamadas vêm vazias — não é falha, é conta parada."""
        assert insight_vazio_com_entrega(insights_upserted=0, placement_upserted=0) is False

    def test_nao_acusa_sync_saudavel(self):
        assert insight_vazio_com_entrega(insights_upserted=22, placement_upserted=30) is False

    def test_nao_acusa_quando_o_placement_e_que_falhou(self):
        """Placement fora do ar (migration 051 ausente, p.ex.) não é problema de gasto."""
        assert insight_vazio_com_entrega(insights_upserted=12, placement_upserted=0) is False
