"""O gate de homologação vale nos endpoints de sync manual, não só na função pura.

Cobre o caminho HTTP inteiro: quem não está liberado toma 403 antes de
qualquer chamada à Shopee/Meta.
"""

from starlette.testclient import TestClient

from app.api.v1.dependencies import require_active_subscription
from app.core.config import settings
from app.db.session import get_db
from app.main import app

URL_HML = "postgresql://postgres.ytjpdvjuxtvxacredekk:senha@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"
URL_PROD = "postgresql://postgres.iprdyorxqdiivthtcvxf:senha@aws-0-sa-east-1.pooler.supabase.com:5432/postgres"

LUIZ = "lfernandooliveira@outlook.com"


class _FakeUser:
    def __init__(self, email: str):
        self.id = 1
        self.email = email
        self.is_demo = False


def _client(email: str) -> TestClient:
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[require_active_subscription] = lambda: _FakeUser(email)
    return TestClient(app)


def test_shopee_sync_bloqueia_outra_conta_em_homologacao(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", URL_HML)
    try:
        resp = _client("relacionamento@marketdash.com.br").post("/api/v1/shopee/sync?days=7")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert "Luiz Fernando" in resp.json()["detail"]


def test_facebook_sync_bloqueia_outra_conta_em_homologacao(monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_URL", URL_HML)
    try:
        resp = _client("relacionamento@marketdash.com.br").post("/api/v1/facebook/sync")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert "Luiz Fernando" in resp.json()["detail"]


def test_shopee_sync_do_luiz_passa_do_gate_em_homologacao(monkeypatch):
    """Passar do gate ≠ sincronizar: sem integração configurada o 404 é o esperado.

    O que importa aqui é NÃO ser 403 — o gate deixou a conta do Luiz seguir.
    """
    monkeypatch.setattr(settings, "DATABASE_URL", URL_HML)
    monkeypatch.setattr(
        "app.api.v1.routes.shopee.ShopeeIntegrationService.get_status",
        lambda self, user_id: None,
    )
    try:
        resp = _client(LUIZ).post("/api/v1/shopee/sync?days=7")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


def test_producao_nao_bloqueia_ninguem(monkeypatch):
    """O gate é de homologação. Ligar em produção pararia o sync de todas as alunas."""
    monkeypatch.setattr(settings, "DATABASE_URL", URL_PROD)
    monkeypatch.setattr(
        "app.api.v1.routes.shopee.ShopeeIntegrationService.get_status",
        lambda self, user_id: None,
    )
    try:
        resp = _client("aluna@exemplo.com").post("/api/v1/shopee/sync?days=7")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
