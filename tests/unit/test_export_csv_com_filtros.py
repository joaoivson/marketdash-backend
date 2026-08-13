"""Rodada 7, item 8 (backend): export.csv usa os mesmos filtros da lista,
não a base inteira sem filtro."""
from starlette.testclient import TestClient

from app.main import app
from app.api.v1.dependencies import get_current_user, require_admin
from app.db.session import get_db


def test_export_csv_repassa_filtros_para_list_clients(monkeypatch):
    capturado = {}

    class _FakeSvc:
        def __init__(self, db):
            pass

        def list_clients(self, filters):
            capturado["filters"] = filters
            return []

    monkeypatch.setattr("app.api.v1.routes.admin_panel.AdminMetricsService", _FakeSvc)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[require_admin] = lambda: object()
    try:
        client = TestClient(app)
        client.get("/api/v1/admin/clients/export.csv?status=inativo&plan=pro&no_login_10d=true")
    finally:
        app.dependency_overrides.clear()

    assert capturado["filters"]["status"] == "inativo"
    assert capturado["filters"]["plan"] == "pro"
    assert capturado["filters"]["no_login_10d"] is True
