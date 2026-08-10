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


# --- provisionamento da instância -------------------------------------------
#
# Estes caminhos foram exercitados contra uma Evolution real subida localmente;
# os mocks abaixo existem para travar o comportamento contra regressão.

import httpx

from app.services.evolution_client import ErroWhatsapp, EvolutionClient


def _cliente(responder):
    c = EvolutionClient("http://evolution", "chave", "marketdash")
    c._transport = httpx.MockTransport(responder)
    return c


def test_instancia_ja_existente_nao_e_erro():
    # A tela do admin chama isto toda vez que abre; 403 "already in use" é o
    # caso normal a partir da segunda vez.
    def responder(req):
        return httpx.Response(403, json={"response": {"message": ["already in use"]}})

    assert _cliente(responder).criar_instancia() == {"ja_existia": True}


def test_falha_de_verdade_ao_criar_instancia_sobe_tipada():
    def responder(req):
        return httpx.Response(500, json={"message": "boom"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).criar_instancia()
    assert e.value.motivo == "criar_instancia"


def test_chave_invalida_continua_sendo_auth():
    def responder(req):
        return httpx.Response(401, json={"message": "Unauthorized"})

    with pytest.raises(ErroWhatsapp) as e:
        _cliente(responder).estado()
    assert e.value.motivo == "auth" and e.value.fatal


def test_webhook_manda_token_e_so_o_evento_de_mensagem():
    capturado = {}

    def responder(req):
        import json as _json
        capturado.update(_json.loads(req.content))
        return httpx.Response(200, json={"ok": True})

    _cliente(responder).configurar_webhook("https://api/x/webhook", "segredo")
    wh = capturado["webhook"]
    assert wh["enabled"] is True
    assert wh["url"] == "https://api/x/webhook"
    assert wh["headers"]["X-Webhook-Token"] == "segredo"
    assert wh["events"] == ["MESSAGES_UPSERT"]


# --- URL do webhook atrás de proxy ------------------------------------------

def test_url_do_webhook_respeita_o_proto_do_proxy():
    """
    Bug real em homologação: a Evolution recebeu `http://api.hml...`, que
    responde 301, e não segue redirecionamento. O SAIR nunca chegou e a
    afiliada continuou recebendo — falha silenciosa, do tipo que só aparece
    como denúncia.
    """
    from types import SimpleNamespace
    from app.api.v1.routes.whatsapp import url_do_webhook

    def req(headers):
        return SimpleNamespace(
            headers=headers,
            url_for=lambda nome: "http://api.hml.marketdash.com.br/api/v1/whatsapp/webhook",
        )

    assert url_do_webhook(req({"x-forwarded-proto": "https"})).startswith("https://")
    # cadeia de proxies: vale o primeiro
    assert url_do_webhook(req({"x-forwarded-proto": "https, http"})).startswith("https://")
    # sem proxy (dev local) mantém o que veio
    assert url_do_webhook(req({})).startswith("http://")
    # valor lixo não vira esquema
    assert url_do_webhook(req({"x-forwarded-proto": "banana"})).startswith("http://")


def test_webhook_atual_devolve_vazio_quando_nao_ha():
    def responder(req):
        return httpx.Response(404, json={"message": "not found"})

    assert _cliente(responder).webhook_atual() == {}
