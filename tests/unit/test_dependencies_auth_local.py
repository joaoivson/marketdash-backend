"""get_current_user: caminho local x fallback auth.get_user.
Run: pytest tests/unit/test_dependencies_auth_local.py -v
"""
from unittest.mock import Mock, patch

import pytest
from fastapi import HTTPException

from app.api.v1 import dependencies as deps
from app.core import supabase_jwt


def test_local_valido_nao_chama_supabase():
    with patch.object(deps.settings, "AUTH_VALIDACAO_LOCAL", True), \
         patch.object(supabase_jwt, "verificar_token", return_value={"email": "ana@x.com"}), \
         patch.object(deps, "get_supabase_client") as cliente:
        assert deps._email_do_token("tok") == "ana@x.com"
    cliente.assert_not_called()


def test_local_invalido_rejeita_sem_fallback():
    """Token inválido NÃO pode cair no get_user (seria uma chamada de rede a mais por token ruim)."""
    with patch.object(deps.settings, "AUTH_VALIDACAO_LOCAL", True), \
         patch.object(supabase_jwt, "verificar_token", side_effect=supabase_jwt.TokenInvalido("exp")), \
         patch.object(deps, "get_supabase_client") as cliente:
        with pytest.raises(HTTPException) as exc:
            deps._email_do_token("tok")
    assert exc.value.status_code == 401
    cliente.assert_not_called()


def test_sem_chave_configurada_usa_get_user():
    fake = Mock()
    fake.auth.get_user.return_value = Mock(user=Mock(email="bia@x.com"))
    with patch.object(deps.settings, "AUTH_VALIDACAO_LOCAL", True), \
         patch.object(supabase_jwt, "verificar_token", side_effect=supabase_jwt.TokenNaoVerificavelLocalmente("x")), \
         patch.object(deps, "get_supabase_client", return_value=fake):
        assert deps._email_do_token("tok") == "bia@x.com"
    fake.auth.get_user.assert_called_once_with("tok")


def test_flag_desligada_mantem_comportamento_antigo():
    fake = Mock()
    fake.auth.get_user.return_value = Mock(user=Mock(email="cla@x.com"))
    with patch.object(deps.settings, "AUTH_VALIDACAO_LOCAL", False), \
         patch.object(supabase_jwt, "verificar_token") as local, \
         patch.object(deps, "get_supabase_client", return_value=fake):
        assert deps._email_do_token("tok") == "cla@x.com"
    local.assert_not_called()
