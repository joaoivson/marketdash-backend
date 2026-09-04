"""
Código de pareamento — conectar um número SEM QR.

A afiliada abre o MarketDash no celular e o WhatsApp que ela vai conectar é o
do MESMO aparelho: não há como escanear o QR da própria tela. Sem isso, parte
do público não conecta e some sem dizer por quê.

O contrato é o mesmo do QR (`estado` + o dado do pareamento) porque a tela usa
o poll do QR como detector de conexão nos dois modos — divergir aqui faria o
modo código nunca perceber que pareou.
"""
import httpx
import pytest

from app.services.waha_client import WahaClient
from app.services.whatsapp_instancia_service import (
    NumeroInvalido, WhatsappInstanciaService,
)


def _cliente(responder):
    c = WahaClient("http://waha:3000", "chave", "mkdtestu1xabcd")
    c._transport = httpx.MockTransport(responder)
    return c


def test_pede_o_codigo_com_o_numero_em_e164_sem_mais():
    visto = {}

    def responder(req):
        visto["url"] = str(req.url)
        visto["corpo"] = __import__("json").loads(req.content)
        return httpx.Response(200, json={"code": "ABCD1234"})

    # Digitado como gente digita — o cliente normaliza antes de enviar.
    codigo = _cliente(responder).codigo_de_pareamento("(11) 99999-8888")

    assert codigo == "ABCD1234"
    assert visto["url"].endswith("/api/mkdtestu1xabcd/auth/request-code")
    assert visto["corpo"] == {"phoneNumber": "5511999998888"}


def test_resposta_sem_codigo_devolve_none_em_vez_de_erro():
    """Sessão ainda subindo não tem código — é estado normal, não falha. Quem
    chama decide o que exibir (a tela pede de novo em instantes)."""
    responder = lambda req: httpx.Response(200, json={})
    assert _cliente(responder).codigo_de_pareamento("11999998888") is None

    responder_422 = lambda req: httpx.Response(422, json={"error": "not ready"})
    assert _cliente(responder_422).codigo_de_pareamento("11999998888") is None


class _Instancia:
    nome_instancia = "mkdtestu1xabcd"
    id = 1
    user_id = 1


def _servico(monkeypatch, estado, codigo="ABCD1234", info=None):
    servico = WhatsappInstanciaService.__new__(WhatsappInstanciaService)
    servico.webhook_url = None
    servico.db = None

    class _Cliente:
        def sessao_info(self):
            return info if info is not None else {"status": estado}

        def criar_sessao(self, **kwargs):
            return {}

        def iniciar_sessao(self):
            chamadas.append("iniciar")

        def codigo_de_pareamento(self, numero):
            chamadas.append(("codigo", numero))
            return codigo

    chamadas = []
    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.cliente_da_sessao",
        lambda _nome: _Cliente(),
    )
    return servico, chamadas


def test_numero_invalido_falha_antes_de_falar_com_o_waha(monkeypatch):
    """Mensagem própria para o número, e não "erro: sessao" — a afiliada
    precisa saber que o problema é o que ela digitou."""
    servico, chamadas = _servico(monkeypatch, "SCAN_QR_CODE")
    with pytest.raises(NumeroInvalido):
        servico.codigo_de_pareamento(_Instancia(), "1132224444")  # fixo, não celular
    assert chamadas == []


def test_sessao_ja_conectada_nao_pede_codigo(monkeypatch):
    servico, chamadas = _servico(
        monkeypatch, "WORKING", info={"status": "WORKING", "me": {"id": "5511999998888@c.us"}}
    )
    servico._marcar_conectada = lambda *a, **k: None
    r = servico.codigo_de_pareamento(_Instancia(), "11999998888")
    assert r == {"estado": "conectada", "codigo": None}
    assert chamadas == []


def test_sessao_parada_e_religada_e_o_codigo_fica_para_o_proximo_toque(monkeypatch):
    """O WAHA só emite o código depois que a sessão chega em SCAN_QR_CODE.
    Pedir agora devolveria None com cara de erro."""
    servico, chamadas = _servico(monkeypatch, "STOPPED")
    r = servico.codigo_de_pareamento(_Instancia(), "11999998888")
    assert r == {"estado": "aguardando", "codigo": None}
    assert chamadas == ["iniciar"]


def test_caminho_feliz_devolve_o_codigo(monkeypatch):
    servico, chamadas = _servico(monkeypatch, "SCAN_QR_CODE")
    r = servico.codigo_de_pareamento(_Instancia(), "+55 11 99999-8888")
    assert r == {"estado": "aguardando", "codigo": "ABCD1234"}
    assert chamadas == [("codigo", "5511999998888")]
