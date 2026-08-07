"""
Normalização do número e leitura do SAIR.

Número errado aqui significa mandar mensagem diária para um desconhecido — a
via mais curta para o número do MarketDash ser denunciado.
"""
import pytest

from app.services.evolution_client import mascarar, normalizar_numero
from app.services.whatsapp_optin_service import pediu_para_sair


@pytest.mark.parametrize("entrada,esperado", [
    ("11999998888", "5511999998888"),
    ("(11) 99999-8888", "5511999998888"),
    ("+55 11 99999-8888", "5511999998888"),
    ("55 11 99999 8888", "5511999998888"),
    ("  11 9 9999-8888  ", "5511999998888"),
    ("5511999998888", "5511999998888"),
])
def test_formatos_que_gente_digita_viram_e164(entrada, esperado):
    assert normalizar_numero(entrada) == esperado


@pytest.mark.parametrize("entrada", [
    "", "   ", "abc",
    "1199998888",        # fixo (10 dígitos) — não recebe WhatsApp
    "11899998888",       # nono dígito não é 9
    "999998888",         # sem DDD
    "5511999998888999",  # comprido demais
])
def test_numero_que_nao_serve_e_recusado(entrada):
    with pytest.raises(ValueError):
        normalizar_numero(entrada)


def test_mascara_nunca_mostra_o_numero_inteiro():
    m = mascarar("5511999998888")
    assert "99999" not in m
    assert m.startswith("5511") and m.endswith("88")


@pytest.mark.parametrize("texto", [
    "SAIR", "sair", "  Sair  ", "sair.", "quero sair",
    "PARAR", "cancelar", "stop", "descadastrar",
])
def test_pedidos_de_saida_sao_reconhecidos(texto):
    assert pediu_para_sair(texto) is True


@pytest.mark.parametrize("texto", [
    None, "", "oi", "obrigada!", "como saio do vermelho?",
    "vou sairei amanhã",   # "sairei" não é "sair"
])
def test_conversa_normal_nao_desliga_ninguem(texto):
    assert pediu_para_sair(texto) is False
