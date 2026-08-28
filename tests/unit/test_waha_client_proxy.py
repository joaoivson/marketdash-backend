"""
O `config` da sessão com proxy — contra MockTransport.

Dois defeitos silenciosos moram aqui, e por isso este arquivo existe:

  1. `webhooks` e `proxy` moram na MESMA chave (`config`). Escrever um
     apagava o outro: o PUT de webhooks (que acontece toda vez que alguém liga
     ou desliga um monitoramento) devolveria a sessão para o IP do servidor,
     sem erro nenhum;
  2. credencial de proxy em log. O `config` inteiro já é logado em outros
     pontos do sistema — usuário e senha do IP não podem viajar junto.
"""
import logging

import httpx
import pytest

from app.services.waha_client import ErroWhatsapp, WahaClient, mascarar_proxy

PROXY = {"server": "10.0.0.1:8001", "username": "usuaria", "password": "s3nh4-secreta"}
WEBHOOKS = [{"url": "https://api/x/webhook", "events": ["session.status"]}]


def _cliente(responder):
    c = WahaClient("http://waha:3000", "chave", "mkdtestu1xabcd")
    c._transport = httpx.MockTransport(responder)
    return c


def test_criar_sessao_manda_webhooks_e_proxy_juntos():
    visto = {}

    def responder(req):
        visto.update(__import__("json").loads(req.content))
        return httpx.Response(201, json={})

    _cliente(responder).criar_sessao(webhooks=WEBHOOKS, proxy=PROXY)
    config = visto["config"]
    assert config["webhooks"] == WEBHOOKS
    assert config["proxy"] == PROXY


def test_put_de_webhooks_nao_apaga_o_proxy():
    """O caso que quebra em produção: ligar um monitoramento reescreve o
    `config` e a sessão volta a sair pelo IP do servidor, em silêncio."""
    visto = {}

    def responder(req):
        visto.update(__import__("json").loads(req.content))
        return httpx.Response(200, json={})

    _cliente(responder).atualizar_sessao(WEBHOOKS, proxy=PROXY)
    assert visto["config"]["proxy"] == PROXY
    assert visto["config"]["webhooks"] == WEBHOOKS


def test_server_do_proxy_vai_sem_esquema():
    """`http://host:porta` é recusado pelo WAHA — o campo é `host:porta`."""
    visto = {}

    def responder(req):
        visto.update(__import__("json").loads(req.content))
        return httpx.Response(201, json={})

    _cliente(responder).criar_sessao(webhooks=None, proxy=PROXY)
    assert not visto["config"]["proxy"]["server"].startswith("http")


def test_sessao_sem_proxy_nao_manda_a_chave():
    """Mandar `proxy: null` faz o WAHA responder 422 — o mesmo tipo de armadilha
    do `hmac: null` que já mordeu a criação de sessão."""
    visto = {}

    def responder(req):
        visto.update(__import__("json").loads(req.content))
        return httpx.Response(201, json={})

    _cliente(responder).criar_sessao(webhooks=WEBHOOKS, proxy=None)
    assert "proxy" not in visto["config"]


def test_credencial_do_proxy_nao_aparece_no_log(caplog):
    def responder(req):
        return httpx.Response(201, json={})

    with caplog.at_level(logging.DEBUG):
        _cliente(responder).criar_sessao(webhooks=WEBHOOKS, proxy=PROXY)
    assert "s3nh4-secreta" not in caplog.text
    assert "usuaria" not in caplog.text


def test_mascarar_proxy_troca_a_credencial_por_estrela():
    limpo = mascarar_proxy({"webhooks": WEBHOOKS, "proxy": PROXY})
    assert limpo["proxy"] == "***"
    assert limpo["webhooks"] == WEBHOOKS      # o resto continua legível


def test_parar_sessao_aceita_sessao_ja_parada():
    """`stop` numa sessão parada (422) ou inexistente (404) é sucesso para
    quem só quer garantir que ela não está rodando."""
    for status in (200, 404, 422):
        _cliente(lambda req, s=status: httpx.Response(s, json={})).parar_sessao()


def test_parar_sessao_sobe_erro_de_verdade():
    with pytest.raises(ErroWhatsapp) as e:
        _cliente(lambda req: httpx.Response(500, json={"message": "boom"})).parar_sessao()
    assert e.value.motivo == "sessao"
