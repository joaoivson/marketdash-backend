"""Incidente de 08/09/2026: o Supabase de produção degradou (504 em
`/auth/v1/*`) e o login passou a devolver "Erro na migração de conta. Por
favor, use 'Esqueci minha senha'" — a MESMA mensagem de quando a conta já
existe no Supabase com outra senha.

A senha da aluna estava correta (o passo 2 já a conferiu contra o banco
local). A mensagem mandava trocar uma senha certa, e o log em nível `info`
("usuário pode não estar migrado") apagava o rastro do timeout.

Estes testes fixam a distinção: indisponibilidade → 503 dizendo que a senha
está correta; erro de estado → o 401 de sempre. E, principalmente, que senha
errada continua sendo senha errada mesmo com o Supabase fora.
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from app.services.auth_service import AuthService, falha_de_indisponibilidade


# --------------------------------------------------------------------------
# O classificador
# --------------------------------------------------------------------------


def test_erro_retentavel_da_biblioteca_e_indisponibilidade():
    """`AuthRetryableError` é o que a supabase-auth devolve para 502/503/504
    e para exceção de rede — é a classificação dela, não heurística nossa."""
    from supabase_auth.errors import AuthRetryableError

    assert falha_de_indisponibilidade(AuthRetryableError("upstream request timeout", 504))
    assert falha_de_indisponibilidade(AuthRetryableError("connection refused", 0))


def test_status_de_gateway_e_indisponibilidade():
    from supabase_auth.errors import AuthApiError

    for codigo in (408, 429, 500, 502, 503, 504, 520, 524):
        assert falha_de_indisponibilidade(AuthApiError("erro", codigo, None)), codigo


def test_timeout_de_httpx_e_indisponibilidade():
    import httpx

    assert falha_de_indisponibilidade(httpx.ReadTimeout("timed out"))
    assert falha_de_indisponibilidade(httpx.ConnectError("sem rota"))


def test_credencial_e_estado_nao_sao_indisponibilidade():
    """O default seguro é 401. Dizer "instabilidade" para quem errou a senha
    esconde da pessoa o motivo real do erro."""
    from supabase_auth.errors import AuthApiError

    assert not falha_de_indisponibilidade(AuthApiError("Invalid login credentials", 400, "invalid_credentials"))
    assert not falha_de_indisponibilidade(AuthApiError("User already registered", 422, "email_exists"))
    assert not falha_de_indisponibilidade(ValueError("qualquer outra coisa"))


# --------------------------------------------------------------------------
# O fluxo de login
# --------------------------------------------------------------------------


def _servico():
    """AuthService com um usuário local válido e ativo."""
    usuario = MagicMock(id=7, email="aluna@exemplo.com", is_active=True, hashed_password="hash")
    repo = MagicMock()
    repo.get_by_email.return_value = usuario
    return AuthService(repo), usuario


def _cliente_supabase(erro_passo_1, erro_migracao):
    """create_client falso: o passo 1 e a Admin API levantam o que o teste pedir."""
    cliente = MagicMock()
    cliente.auth.sign_in_with_password.side_effect = erro_passo_1
    cliente.auth.admin.create_user.side_effect = erro_migracao
    return MagicMock(return_value=cliente)


def _login(servico, erro_passo_1, erro_migracao, senha_confere=True):
    with patch("supabase.create_client", _cliente_supabase(erro_passo_1, erro_migracao)), \
         patch("app.services.auth_service.verify_password", return_value=senha_confere), \
         patch("app.services.auth_service.settings") as cfg:
        cfg.ENVIRONMENT = "development"  # pula a checagem de assinatura (passo 3)
        cfg.CAKTO_ENFORCE_SUBSCRIPTION = False
        cfg.ENFORCE_SUBSCRIPTION = False
        with pytest.raises(HTTPException) as exc:
            servico.login("aluna@exemplo.com", "senha-correta")
        return exc.value


def test_indisponibilidade_na_migracao_devolve_503_e_diz_que_a_senha_esta_certa():
    from supabase_auth.errors import AuthRetryableError

    servico, _ = _servico()
    erro = AuthRetryableError("upstream request timeout", 504)
    resultado = _login(servico, erro_passo_1=erro, erro_migracao=erro)

    assert resultado.status_code == 503
    assert "senha está correta" in resultado.detail
    # o conselho que causou o dano NÃO pode aparecer aqui
    assert "Esqueci minha senha" not in resultado.detail


def test_conta_ja_existente_no_supabase_mantem_o_401_de_migracao():
    """Aqui trocar a senha resolve de verdade — a mensagem antiga está certa."""
    from supabase_auth.errors import AuthApiError

    servico, _ = _servico()
    resultado = _login(
        servico,
        erro_passo_1=AuthApiError("Invalid login credentials", 400, "invalid_credentials"),
        erro_migracao=AuthApiError("User already registered", 422, "email_exists"),
    )

    assert resultado.status_code == 401
    assert "Esqueci minha senha" in resultado.detail


def test_indisponibilidade_no_passo_1_classifica_falha_ambigua_da_migracao_como_503():
    """Se o Supabase já não respondeu no passo 1, uma falha sem tipo no passo 4
    é quase certamente o mesmo timeout. Errar para 503 é o lado seguro: não
    manda ninguém trocar senha à toa, e a tentativa seguinte (com o Supabase
    de pé) devolve o 401 correto se a causa for mesmo de estado."""
    from supabase_auth.errors import AuthRetryableError

    servico, _ = _servico()
    resultado = _login(
        servico,
        erro_passo_1=AuthRetryableError("connection refused", 0),
        erro_migracao=RuntimeError("erro sem tipo nem status"),
    )

    assert resultado.status_code == 503


def test_senha_errada_continua_401_de_credencial_mesmo_com_o_supabase_fora():
    """Regressão que importa: a classificação de infraestrutura não pode
    mascarar credencial inválida. Quem errou a senha precisa saber disso."""
    from supabase_auth.errors import AuthRetryableError

    servico, _ = _servico()
    erro = AuthRetryableError("upstream request timeout", 504)
    resultado = _login(servico, erro_passo_1=erro, erro_migracao=erro, senha_confere=False)

    assert resultado.status_code == 401
    assert resultado.detail == "Email ou senha incorretos"


def test_com_o_classificador_desligado_volta_o_comportamento_antigo():
    """Controle: prova que é a classificação que produz o 503, e não algum
    outro caminho do método. Com ela sempre `False`, o timeout volta a virar
    o 401 "use 'Esqueci minha senha'" — exatamente o bug de 08/09."""
    from supabase_auth.errors import AuthRetryableError

    servico, _ = _servico()
    erro = AuthRetryableError("upstream request timeout", 504)
    with patch("app.services.auth_service.falha_de_indisponibilidade", return_value=False):
        resultado = _login(servico, erro_passo_1=erro, erro_migracao=erro)

    assert resultado.status_code == 401
    assert "Esqueci minha senha" in resultado.detail


def test_timeout_real_do_cliente_supabase_escapa_como_httpx_e_e_classificado():
    """Medido em 09/09/2026 contra um IP não-roteável: `sign_in_with_password`
    levanta `httpx.ConnectTimeout` CRU, sem passar pelo `handle_exception` que
    embrulharia em `AuthRetryableError`. Sem o ramo de httpx no classificador,
    o caso mais comum de indisponibilidade escaparia da detecção."""
    import httpx

    assert falha_de_indisponibilidade(httpx.ConnectTimeout("timed out"))
    assert falha_de_indisponibilidade(httpx.ReadTimeout("timed out"))
    assert falha_de_indisponibilidade(httpx.ConnectError("sem rota para o host"))
