"""O AppID da Shopee é numérico. Salvar qualquer texto deixava a integração
"conectada" na tela com TODA sync falhando em erro genérico da Shopee, sem o
usuário saber por quê — 2 das 3 contas quebradas em produção (28/07/2026) tinham
o e-mail do cliente salvo no campo AppID."""
import pytest
from pydantic import ValidationError

from app.schemas.shopee_integration import ShopeeCredentialsUpsert


def test_accepts_numeric_app_id():
    c = ShopeeCredentialsUpsert(app_id="18191340007", password="segredo")
    assert c.app_id == "18191340007"


def test_trims_whitespace_before_validating():
    c = ShopeeCredentialsUpsert(app_id="  18191340007  ", password="segredo")
    assert c.app_id == "18191340007"


@pytest.mark.parametrize(
    "bad",
    [
        "gabriela_santos94@hotmail.com",  # caso real em produção (user 12)
        "machado.e.carine@gmail.com",  # caso real em produção (user 24)
        "meu_usuario",
        "1819 1340007",
        "18191340007a",
        "",
    ],
)
def test_rejects_non_numeric_app_id(bad):
    with pytest.raises(ValidationError):
        ShopeeCredentialsUpsert(app_id=bad, password="segredo")


def test_error_message_is_actionable():
    with pytest.raises(ValidationError) as exc:
        ShopeeCredentialsUpsert(app_id="cliente@email.com", password="segredo")
    msg = str(exc.value)
    assert "AppID" in msg
    assert "e-mail" in msg  # diz explicitamente o erro que as pessoas cometem
