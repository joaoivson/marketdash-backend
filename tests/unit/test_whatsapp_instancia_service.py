"""Provisionamento de sessão: nome com prefixo do ambiente, limite do plano e cap global."""
import pytest

from app.services.waha_client import ErroWhatsapp
from app.services.whatsapp_instancia_service import (
    LimiteDeNumeros, LimiteGlobal, WhatsappInstanciaService, nome_de_instancia,
)


class _FakeRepo:
    def __init__(self, ativas=0, global_ativas=0):
        self._ativas = ativas
        self._global = global_ativas
        self.salvas = []

    def por_usuario(self, user_id):
        return [object()] * self._ativas

    def total_global_ativas(self):
        return self._global

    def salvar(self, instancia):
        instancia.id = len(self.salvas) + 1
        self.salvas.append(instancia)
        return instancia


def test_nome_tem_prefixo_do_ambiente_e_user_id(monkeypatch):
    monkeypatch.setattr("app.services.whatsapp_instancia_service.identidade_do_banco",
                        lambda: "ytjpdvjuxtvxacredekk")
    nome = nome_de_instancia(42)
    assert nome.startswith("mkdytjpu42x")
    assert len(nome) == len("mkdytjpu42x") + 4   # sufixo hex4


def test_limite_do_plano_barra_o_quarto_numero(monkeypatch):
    svc = WhatsappInstanciaService(_FakeRepo(ativas=3), plan_limit_numeros=3,
                                   webhook_url=None)
    with pytest.raises(LimiteDeNumeros):
        svc.criar(1, "Número 4")


def test_plano_sem_o_recurso_e_plano_insuficiente():
    svc = WhatsappInstanciaService(_FakeRepo(), plan_limit_numeros=0, webhook_url=None)
    with pytest.raises(LimiteDeNumeros) as e:
        svc.criar(1, None)
    assert "PLANO_INSUFICIENTE" in str(e.value)


def test_cap_global_protege_a_ram_do_servidor(monkeypatch):
    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.settings.WHATSAPP_MAX_INSTANCIAS_GLOBAL",
        2, raising=False,
    )
    svc = WhatsappInstanciaService(_FakeRepo(ativas=1, global_ativas=2),
                                   plan_limit_numeros=3, webhook_url=None)
    with pytest.raises(LimiteGlobal):
        svc.criar(1, None)


def test_criar_provisiona_sessao_no_waha(monkeypatch):
    criadas = []

    class _Cliente:
        def criar_sessao(self, webhooks=None, start=True):
            criadas.append(webhooks)
            return {}

    monkeypatch.setattr("app.services.whatsapp_instancia_service.cliente_da_sessao",
                        lambda nome: _Cliente())
    repo = _FakeRepo()
    svc = WhatsappInstanciaService(repo, plan_limit_numeros=3,
                                   webhook_url="https://api/x/webhook")
    inst = svc.criar(7, "  Meu número  ")

    assert inst.nome_exibicao == "Meu número"
    assert inst.user_id == 7
    assert len(criadas) == 1
    # Sessão de aluna nasce SÓ com session.status — sem conteúdo de mensagem.
    assert criadas[0][0]["events"] == ["session.status"]
    assert criadas[0][0]["url"] == "https://api/x/webhook"


def test_falha_do_waha_ao_criar_nao_persiste_linha(monkeypatch):
    # Linha órfã local consumiria o limite do plano: 3 tentativas num outage
    # do WAHA e a usuária ficaria trancada com zero números funcionais.
    class _ClienteQueFalha:
        def criar_sessao(self, webhooks=None, start=True):
            raise ErroWhatsapp("timeout", "WAHA fora do ar")

    monkeypatch.setattr("app.services.whatsapp_instancia_service.cliente_da_sessao",
                        lambda nome: _ClienteQueFalha())
    repo = _FakeRepo()
    svc = WhatsappInstanciaService(repo, plan_limit_numeros=3, webhook_url="https://api/x")
    with pytest.raises(ErroWhatsapp):
        svc.criar(1, "Número 1")
    assert repo.salvas == []


def test_qr_de_sessao_parada_religa_em_vez_de_esperar_para_sempre(monkeypatch):
    religadas = []

    class _ClienteParado:
        def sessao_info(self):
            return {"status": "STOPPED"}

        def iniciar_sessao(self):
            religadas.append(True)

    monkeypatch.setattr("app.services.whatsapp_instancia_service.cliente_da_sessao",
                        lambda nome: _ClienteParado())
    from types import SimpleNamespace
    svc = WhatsappInstanciaService(_FakeRepo(), plan_limit_numeros=3, webhook_url=None)
    r = svc.qr(SimpleNamespace(nome_instancia="mkdXu1xaaaa", status="conectada",
                               numero=None, falhas_seguidas=0, ultima_conexao_em=None))
    assert religadas == [True]
    assert r == {"estado": "aguardando", "qrcode": None}


def test_config_de_webhook_nao_emite_chaves_none(monkeypatch):
    # hmac/customHeaders None derrubam a criação com 422 no WAHA.
    monkeypatch.setattr("app.services.whatsapp_instancia_service.settings.WAHA_WEBHOOK_TOKEN",
                        None, raising=False)
    from app.services.whatsapp_instancia_service import config_de_webhook
    wh = config_de_webhook("https://api/x")[0]
    assert "hmac" not in wh and "customHeaders" not in wh

    monkeypatch.setattr("app.services.whatsapp_instancia_service.settings.WAHA_WEBHOOK_TOKEN",
                        "segredo", raising=False)
    wh = config_de_webhook("https://api/x", ["message"])[0]
    assert wh["hmac"] == {"key": "segredo"}
    assert wh["customHeaders"] == [{"name": "X-Webhook-Token", "value": "segredo"}]
    assert wh["events"] == ["message"]
