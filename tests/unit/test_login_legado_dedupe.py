"""Rodada 7, item 4: login legado (fallback pré-Supabase) passa a usar
record_access() — mesma janela de dedupe de 2min do fluxo principal.
Hoje grava UserLogin direto, sem proteção nenhuma."""
from unittest.mock import MagicMock, patch

from app.api.v1.routes import auth as auth_routes


def test_login_legado_usa_record_access_nao_insert_direto():
    with patch.object(auth_routes, "AuthService") as AuthServiceMock:
        AuthServiceMock.return_value.login.return_value = {
            "user": MagicMock(id=42),
        }
        with patch("app.services.daily_access_service.record_access") as record_access_mock:
            db = MagicMock()
            http_request = MagicMock()
            http_request.client.host = "1.2.3.4"
            http_request.headers.get.return_value = "pytest-agent"

            from app.schemas.user import LoginRequest

            auth_routes.login(
                LoginRequest(email="a@example.com", password="x"),
                http_request,
                db,
            )

            record_access_mock.assert_called_once_with(
                db, 42, ip="1.2.3.4", user_agent="pytest-agent"
            )
            db.add.assert_not_called()  # não grava UserLogin direto mais
