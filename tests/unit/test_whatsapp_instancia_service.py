"""Provisionamento de sessão: nome com prefixo do ambiente, limite do plano e cap global."""
from types import SimpleNamespace

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
    # Sessão de aluna assina estado + entradas/saídas de participantes (F6).
    # NUNCA `message`: conteúdo de grupo não chega ao backend (LGPD).
    assert criadas[0][0]["events"] == ["session.status", "group.v2.participants"]
    assert "message" not in criadas[0][0]["events"]
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


# --- F8: alinhamento dos eventos da sessão ----------------------------------


class _ClienteDeSessao:
    """Fake do WahaClient para o caminho de reconfiguração."""

    def __init__(self, eventos_atuais, registro):
        self.eventos_atuais = eventos_atuais
        self.registro = registro

    def sessao_info(self):
        return {"config": {"webhooks": [{"url": "https://api/x/webhook",
                                         "events": list(self.eventos_atuais)}]}}

    def atualizar_sessao(self, webhooks):
        self.registro.append(webhooks[0]["events"])
        return {}


class _DbSemEnvio:
    """Sem execução `enviando` — o caminho normal."""

    def query(self, *_a, **_k):
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return None


class _DbComEnvio(_DbSemEnvio):
    def first(self):
        return object()          # há execução enviando


def _com_cliente(monkeypatch, cliente):
    monkeypatch.setattr("app.services.whatsapp_instancia_service.cliente_da_sessao",
                        lambda nome: cliente)


def test_ligar_monitoramento_faz_a_sessao_assinar_message(monkeypatch):
    from app.services.whatsapp_instancia_service import sincronizar_eventos

    feitas = []
    _com_cliente(monkeypatch, _ClienteDeSessao(
        ["session.status", "group.v2.participants"], feitas))
    inst = SimpleNamespace(nome_instancia="mkdaaau1xbbbb", user_id=1, id=1)

    assert sincronizar_eventos(_DbSemEnvio(), inst, True, "https://api/x/webhook") is True
    assert feitas == [["session.status", "group.v2.participants", "message"]]


def test_desligar_monitoramento_remove_message_da_sessao(monkeypatch):
    """O caminho que importa para a privacidade: sem ele a sessão continuaria
    entregando conteúdo de grupo depois de a afiliada desligar."""
    from app.services.whatsapp_instancia_service import sincronizar_eventos

    feitas = []
    _com_cliente(monkeypatch, _ClienteDeSessao(
        ["session.status", "group.v2.participants", "message"], feitas))
    inst = SimpleNamespace(nome_instancia="mkdaaau1xbbbb", user_id=1, id=1)

    assert sincronizar_eventos(_DbSemEnvio(), inst, False, "https://api/x/webhook") is True
    assert feitas == [["session.status", "group.v2.participants"]]


def test_sessao_ja_alinhada_nao_e_reiniciada(monkeypatch):
    """O PUT REINICIA a sessão. Reconfigurar à toa derruba a conexão de um
    número por nada."""
    from app.services.whatsapp_instancia_service import sincronizar_eventos

    feitas = []
    _com_cliente(monkeypatch, _ClienteDeSessao(
        ["group.v2.participants", "session.status"], feitas))   # ordem diferente
    inst = SimpleNamespace(nome_instancia="mkdaaau1xbbbb", user_id=1, id=1)

    assert sincronizar_eventos(_DbSemEnvio(), inst, False, "https://api/x/webhook") is False
    assert feitas == []


def test_envio_em_andamento_recusa_a_reconfiguracao(monkeypatch):
    """Reiniciar a sessão no meio de um lote pararia o envio. As linhas não
    duplicam (o claim garante), mas a afiliada veria o envio morrer sem
    entender por quê — melhor recusar com uma frase clara."""
    from app.services.whatsapp_instancia_service import (
        EnvioEmAndamento, sincronizar_eventos,
    )

    feitas = []
    _com_cliente(monkeypatch, _ClienteDeSessao(
        ["session.status", "group.v2.participants"], feitas))
    inst = SimpleNamespace(nome_instancia="mkdaaau1xbbbb", user_id=1, id=1)

    with pytest.raises(EnvioEmAndamento):
        sincronizar_eventos(_DbComEnvio(), inst, True, "https://api/x/webhook")
    assert feitas == [], "reconfigurou apesar do envio em andamento"


def test_sessao_fora_do_ar_nao_derruba_o_toggle(monkeypatch):
    """Sessão inacessível não pode impedir a afiliada de mexer na configuração;
    o cron diário de reconciliação repara o desalinhamento."""
    from app.services.waha_client import ErroWhatsapp
    from app.services.whatsapp_instancia_service import sincronizar_eventos

    class _Fora:
        def sessao_info(self):
            raise ErroWhatsapp("conexao", "sessão fora do ar")

    _com_cliente(monkeypatch, _Fora())
    inst = SimpleNamespace(nome_instancia="mkdaaau1xbbbb", user_id=1, id=1)
    assert sincronizar_eventos(_DbSemEnvio(), inst, True, "https://api/x/webhook") is False


def test_com_dois_numeros_ou_reconfigura_os_dois_ou_nenhum(monkeypatch):
    """
    Com envio em andamento, reconfigurar o primeiro número e recusar o segundo
    deixaria o banco divergindo do que as sessões realmente escutam — o pior
    estado possível: a tela diria "monitorando" e nada chegaria (ou o contrário).
    """
    from app.services.whatsapp_instancia_service import (
        EnvioEmAndamento, sincronizar_todas,
    )

    feitas = []
    clientes = {
        "a": _ClienteDeSessao(["session.status", "group.v2.participants"], feitas),
        "b": _ClienteDeSessao(["session.status", "group.v2.participants"], feitas),
    }
    monkeypatch.setattr("app.services.whatsapp_instancia_service.cliente_da_sessao",
                        lambda nome: clientes[nome[-1]])
    instancias = [
        SimpleNamespace(nome_instancia="mkdaaau1xbba", user_id=1, id=1),
        SimpleNamespace(nome_instancia="mkdaaau1xbbb", user_id=1, id=2),
    ]

    with pytest.raises(EnvioEmAndamento):
        sincronizar_todas(_DbComEnvio(), instancias, {1: True, 2: True},
                          "https://api/x/webhook")
    assert feitas == [], "reconfigurou uma sessão antes de recusar a outra"

    # Sem envio, as duas mudam.
    assert sincronizar_todas(_DbSemEnvio(), instancias, {1: True, 2: True},
                             "https://api/x/webhook") == 2
    assert len(feitas) == 2
    assert all("message" in ev for ev in feitas)


def test_sessao_inexistente_no_waha_nao_tenta_reconfigurar(monkeypatch):
    """
    `sessao_info()` devolve {} em 404. Tratar isso como "sem webhook, precisa
    reconfigurar" fazia o PUT falhar numa sessão que não existe, e a afiliada
    recebia "não foi possível falar com o WhatsApp" ao ligar um monitoramento —
    sem nenhum caminho para resolver. Quem recria a sessão é o pareamento.
    """
    from app.services.whatsapp_instancia_service import sincronizar_eventos

    feitas = []

    class _Ausente:
        def sessao_info(self):
            return {}

        def atualizar_sessao(self, webhooks):
            feitas.append(webhooks)

    _com_cliente(monkeypatch, _Ausente())
    inst = SimpleNamespace(nome_instancia="mkdaaau1xbbbb", user_id=1, id=1)
    assert sincronizar_eventos(_DbSemEnvio(), inst, True, "https://api/x/webhook") is False
    assert feitas == []
