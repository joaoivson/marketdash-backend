"""
O pseudônimo do participante precisa ser IRREVERSÍVEL — a política de
privacidade promete isso em texto.

A versão anterior era `sha256(jid + (salt or ""))` e seguia sem salt quando a
env não estava setada (que era o caso em TODOS os ambientes). Telefone tem
espaço de busca minúsculo: medido nesta máquina, em Python puro, 1,5 milhão de
hashes/s — o espaço inteiro de celulares brasileiros (~1,08 bilhão) cai em
~11 minutos. O "código irreversível" era reversível.
"""
import hashlib

import pytest

from app.services import grupo_evento_service as ges


JID = "5511987654321@c.us"


def test_hash_nao_e_sha256_puro_do_numero(monkeypatch):
    """O ataque concreto: quem tem o banco tenta sha256 de cada telefone."""
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", None, raising=False)

    guardado = ges.identificador(JID)
    for tentativa in (
        hashlib.sha256(JID.encode()).hexdigest(),
        hashlib.sha256(f"{JID}|".encode()).hexdigest(),
        hashlib.sha256(f"{JID}|None".encode()).hexdigest(),
    ):
        assert guardado != tentativa, "hash reversível por força bruta simples"


def test_sem_salt_explicito_ainda_ha_segredo(monkeypatch):
    """A env é opcional e nunca esteve setada. Depender dela era garantir o
    pior caso — sem ela o segredo é derivado do que o app já exige."""
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", None, raising=False)
    monkeypatch.setattr(ges.settings, "SHOPEE_ENCRYPTION_KEY", "chave-do-app", raising=False)

    assert ges._segredo_do_hash()
    assert ges._segredo_do_hash() != ""


def test_salt_explicito_tem_precedencia(monkeypatch):
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", "  sal-explicito  ", raising=False)
    assert ges._segredo_do_hash() == "sal-explicito"


def test_segredos_diferentes_dao_pseudonimos_diferentes(monkeypatch):
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", "sal-a", raising=False)
    a = ges.identificador(JID)
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", "sal-b", raising=False)
    assert a != ges.identificador(JID)


def test_mesmo_jid_da_o_mesmo_pseudonimo(monkeypatch):
    """Estabilidade é o que casa entrada com saída ("entraram e ficaram")."""
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", "sal", raising=False)
    assert ges.identificador(JID) == ges.identificador(JID)
    assert ges.identificador(JID) != ges.identificador("5511900000000@c.us")


def test_sem_nenhum_segredo_recusa_em_vez_de_degradar(monkeypatch):
    """Falhar alto é melhor que gravar um pseudônimo reversível em silêncio."""
    monkeypatch.setattr(ges.settings, "WHATSAPP_HASH_SALT", None, raising=False)
    monkeypatch.setattr(ges.settings, "SHOPEE_ENCRYPTION_KEY", None, raising=False)
    monkeypatch.setattr(ges.settings, "JWT_SECRET", "", raising=False)
    with pytest.raises(RuntimeError):
        ges.identificador(JID)
