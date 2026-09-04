"""
Rename de e-mail por CPF só em evento que libera acesso.

Bug real (03/09/2026, aluna Anne): ela comprou com o e-mail digitado errado
(`anne.jesus@hormail.com`), recomprou com o certo (`annejesus592@gmail.com`) e
o pedido antigo foi estornado. A ordem dos webhooks foi:

  1. order_approved (e-mail errado)   -> cria o usuário
  2. order_approved (e-mail certo)    -> acha por CPF e renomeia p/ o certo
  3. order_refunded (e-mail ERRADO)   -> acha por CPF e renomeia DE VOLTA
  4. subscription_canceled (e-mail ERRADO) -> idem

No fim a conta paga ficou com o e-mail errado; ao logar com o e-mail certo a
lazy migration criou uma conta NOVA, sem assinatura, e o app mostrou
"Assinatura Necessária" para quem tinha acabado de pagar.
"""
from types import SimpleNamespace

import pytest

from app.services import webhook_helpers

EMAIL_ERRADO = "anne.jesus@hormail.com"
EMAIL_CERTO = "annejesus592@gmail.com"
CPF = "10556446798"


class _FakeRepo:
    """Repositório com um único usuário, indexado por e-mail e por CPF."""

    def __init__(self, user):
        self._user = user

    def get_by_email(self, email):
        return self._user if self._user.email == email else None

    def get_by_cpf(self, cpf):
        return self._user if self._user.cpf_cnpj == cpf else None


@pytest.fixture
def usuario_pagante(monkeypatch):
    user = SimpleNamespace(
        id=75,
        email=EMAIL_ERRADO,
        cpf_cnpj=CPF,
        password_set_token=None,
    )
    monkeypatch.setattr(webhook_helpers, "UserRepository", lambda db: _FakeRepo(user))
    return user


class _FakeDb:
    def commit(self):
        pass

    def refresh(self, _obj):
        pass


def _find(email, allow_email_update):
    return webhook_helpers.find_or_create_user(
        email,
        {"cpf_cnpj": CPF, "email": email, "name": "Anne Caroline da Silva de Jesus"},
        _FakeDb(),
        allow_email_update=allow_email_update,
    )


def test_ativacao_com_email_novo_renomeia_a_conta(usuario_pagante):
    user, criado, _ = _find(EMAIL_CERTO, allow_email_update=True)

    assert criado is False
    assert user.id == 75
    assert user.email == EMAIL_CERTO


def test_estorno_do_pedido_antigo_nao_desfaz_o_rename(usuario_pagante):
    # 1) a recompra corrige o e-mail...
    _find(EMAIL_CERTO, allow_email_update=True)
    # 2) ...e o estorno do pedido velho chega depois, com o e-mail errado.
    user, criado, _ = _find(EMAIL_ERRADO, allow_email_update=False)

    assert criado is False
    assert user.id == 75
    assert user.email == EMAIL_CERTO


def test_match_por_email_nao_depende_da_flag(usuario_pagante):
    user, criado, _ = _find(EMAIL_ERRADO, allow_email_update=False)

    assert criado is False
    assert user.id == 75
    assert user.email == EMAIL_ERRADO
