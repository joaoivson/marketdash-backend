"""Rodada 7, item 5: janela de período alinhada a dia civil BRT.

Antes, uma janela de "7d" cobria pedaços de até 8 datas (UTC, sem
alinhamento a meia-noite civil). Este teste fixa `agora` num horário da
tarde BRT e confere que os logins caem em no máximo 7 dias distintos.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.services.platform_usage_service import PlatformUsageService

BRT = ZoneInfo("America/Sao_Paulo")


def test_inicio_alinha_a_dia_civil_brt(monkeypatch):
    # 12/08/2026, 15h BRT (18h UTC) — meio da tarde, o pior caso pro bug antigo.
    agora_fixo = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            return agora_fixo.astimezone(tz) if tz else agora_fixo

    monkeypatch.setattr(
        "app.services.platform_usage_service.datetime", _DatetimeFixo
    )

    svc = PlatformUsageService(MagicMock())
    inicio = svc._inicio("7d")

    # 7 dias = hoje (06/08 a 12/08 em BRT) + 6 anteriores — início é meia-noite
    # BRT de 06/08, convertida pra UTC (03h UTC).
    esperado = datetime(2026, 8, 6, 0, 0, tzinfo=BRT).astimezone(timezone.utc)
    assert inicio == esperado


def test_usuarias_por_dia_nao_estoura_o_numero_de_dias_da_janela():
    svc = PlatformUsageService(MagicMock())
    svc._ids_admin = lambda: []

    # 8 acessos espalhados por 7 dias civis BRT distintos, incluindo um às
    # 23h30 BRT (02h30 UTC do dia seguinte) — o caso que quebrava o cast em
    # UTC.
    logins = [
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 7, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)),
        # 23h30 BRT de 11/08 = 02h30 UTC de 12/08 — dia civil BRT ainda é 11/08.
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 12, 2, 30, tzinfo=timezone.utc)),
    ]
    query_mock = MagicMock()
    query_mock.with_entities.return_value.all.return_value = [
        (l.logged_at, l.user_id) for l in logins
    ]
    svc._logins_do_periodo = lambda periodo: query_mock

    dias = svc.usuarias_por_dia("7d")

    datas_distintas = {d["date"] for d in dias}
    assert len(datas_distintas) == 6  # não 7 datas UTC diferentes — 11/08 absorve o registro das 2h30
