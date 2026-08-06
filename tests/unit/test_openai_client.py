"""
Fronteira com a OpenAI.

Isolada de propósito: todo o resto do Diagnóstico IA é testado sem rede porque
só este arquivo fala com a API.
"""
import json

import httpx
import pytest

from app.services.openai_client import ErroIA, OpenAiClient


def _resposta(conteudo, entrada=100, saida=50):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": conteudo}}],
            "usage": {"prompt_tokens": entrada, "completion_tokens": saida},
        },
    )


def _cliente(handler, api_key="sk-teste"):
    c = OpenAiClient(api_key=api_key, modelo="gpt-4o-mini")
    c._transport = httpx.MockTransport(handler)
    return c


def test_sem_chave_nao_esta_disponivel():
    assert OpenAiClient(api_key=None, modelo="gpt-4o-mini").disponivel() is False
    assert OpenAiClient(api_key="sk-x", modelo="gpt-4o-mini").disponivel() is True


def test_sem_chave_levanta_erro_tipado():
    c = OpenAiClient(api_key=None, modelo="gpt-4o-mini")
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "sem_chave"


def test_completar_json_devolve_dict_e_tokens():
    payload = {"resumo": "tudo certo", "escalar": []}
    c = _cliente(lambda req: _resposta(json.dumps(payload), 120, 80))
    dados, entrada, saida = c.completar_json("sistema", "usuario")
    assert dados == payload
    assert (entrada, saida) == (120, 80)


def test_json_invalido_levanta_erro_de_formato():
    c = _cliente(lambda req: _resposta("isso não é json"))
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "formato"


def test_erro_http_levanta_erro_tipado():
    c = _cliente(lambda req: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "http"


def test_timeout_levanta_erro_tipado():
    def estoura(req):
        raise httpx.TimeoutException("demorou")
    c = _cliente(estoura)
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "timeout"


def test_completar_texto_devolve_string():
    c = _cliente(lambda req: _resposta("resposta do chat", 10, 5))
    texto, entrada, saida = c.completar_texto(
        "sistema", [{"role": "user", "content": "oi"}]
    )
    assert texto == "resposta do chat"
    assert (entrada, saida) == (10, 5)


def test_modelo_vai_no_corpo_da_requisicao():
    capturado = {}

    def handler(req):
        capturado.update(json.loads(req.content))
        return _resposta(json.dumps({"ok": True}))

    _cliente(handler).completar_json("sistema", "usuario")
    assert capturado["model"] == "gpt-4o-mini"
    assert capturado["messages"][0]["role"] == "system"
