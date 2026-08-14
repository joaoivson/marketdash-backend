"""Rodada 7, item 9 (backend): atividade_por_usuaria expõe o plano de cada
usuária, pra o frontend decidir "0/0" (Pro sem uso) vs "—" (Essencial, sem
o recurso)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.platform_usage_service import PlatformUsageService


def test_atividade_por_usuaria_inclui_plano(monkeypatch):
    svc = PlatformUsageService(MagicMock())
    svc._ids_admin = lambda: []

    login = SimpleNamespace(user_id=7, logged_at=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc))
    query_mock = MagicMock()
    query_mock.with_entities.return_value.all.return_value = [(7, login.logged_at)]
    svc._logins_do_periodo = lambda periodo: query_mock
    svc.uso_de_links_e_paginas = lambda periodo, ids: {}

    subscriber = SimpleNamespace(user_id=7, plan_name="Essencial", plan_id="essencial")

    class _AdminMetricsServiceFake:
        def __init__(self, db):
            pass

        def active_subscribers(self):
            return [subscriber]

    monkeypatch.setattr(
        "app.services.admin_metrics_service.AdminMetricsService", _AdminMetricsServiceFake
    )
    # nomes/emails vêm de `dict(self.db.query(User.id, User.name).filter(...))`
    # — uma lista vazia é um iterável válido de pares pro dict(), sem precisar
    # mockar __iter__ em cima do MagicMock (que não funciona por atribuição
    # direta de instância; magic methods são resolvidos pelo tipo).
    svc.db.query.return_value.filter.return_value = []

    linhas = svc.atividade_por_usuaria("7d")

    assert linhas[0]["plan"] == "essencial"
