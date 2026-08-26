"""
IA de variações (F4). O invariante que carrega a feature: variação que perde
um placeholder é DESCARTADA — mensagem sem {link} quebra a atribuição de
comissão por grupo, que é o produto inteiro.
"""
import json

import httpx
import pytest

from app.services.openai_client import ErroIA, OpenAiClient
from app.services.template_ia_service import (
    TemplateIaService, TextoBaseInvalido,
)


def _cliente(variacoes=None, status=200, corpo=None):
    c = OpenAiClient("chave", "gpt-4o-mini")

    def responder(req):
        if corpo is not None:
            return httpx.Response(status, json=corpo)
        conteudo = json.dumps({"variacoes": variacoes or []})
        return httpx.Response(status, json={
            "choices": [{"message": {"content": conteudo}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        })

    c._transport = httpx.MockTransport(responder)
    return c


BASE = "Corre que acabou de sair: {produto} por {preco_por} 👉 {link}"


def test_variacoes_validas_sao_devolvidas_sem_duplicata():
    ia = TemplateIaService(_cliente([
        "Achadinho do dia: {produto} sai por {preco_por} 👉 {link}",
        "Achadinho do dia: {produto} sai por {preco_por} 👉 {link}",  # duplicada
        "Olha o preço: {produto} — {preco_por}. Pega aqui {link}",
    ]))
    variacoes = ia.gerar_variacoes(BASE, quantidade=3)
    assert len(variacoes) == 2
    assert all("{link}" in v for v in variacoes)


def test_variacao_que_perde_placeholder_e_descartada():
    ia = TemplateIaService(_cliente([
        "Sem link nenhum, só texto de {produto} por {preco_por}",   # perdeu {link}
        "Com tudo: {produto} {preco_por} {link}",
    ]))
    variacoes = ia.gerar_variacoes(BASE)
    assert variacoes == ["Com tudo: {produto} {preco_por} {link}"]


def test_todas_invalidas_sobe_erro_tipado():
    ia = TemplateIaService(_cliente(["nada de marcadores aqui"]))
    with pytest.raises(ErroIA) as e:
        ia.gerar_variacoes(BASE)
    assert e.value.motivo == "formato"


def test_texto_base_curto_e_recusado_antes_de_gastar_token():
    ia = TemplateIaService(_cliente(["x"]))
    with pytest.raises(TextoBaseInvalido):
        ia.gerar_variacoes("oi")


def test_texto_base_sem_placeholder_aceita_qualquer_variacao():
    ia = TemplateIaService(_cliente(["Bom dia!", "Bom dia, gente!"]))
    assert len(ia.gerar_variacoes("Bom dia, pessoal do grupo")) == 2


def test_variacao_e_truncada_no_limite():
    ia = TemplateIaService(_cliente(["x" * 900 + " {link}"]))
    with pytest.raises(ErroIA):
        # truncar em 400 corta o {link} do fim → descartada, não "corrigida"
        ia.gerar_variacoes(BASE)


def test_erro_http_da_openai_sobe_tipado_e_nao_derruba_quem_chama():
    ia = TemplateIaService(_cliente(status=500, corpo={"error": "boom"}))
    with pytest.raises(ErroIA) as e:
        ia.gerar_variacoes(BASE)
    assert e.value.motivo == "http"


def test_sem_chave_a_ia_se_declara_indisponivel():
    ia = TemplateIaService(OpenAiClient(None, "gpt-4o-mini"))
    assert ia.disponivel() is False
