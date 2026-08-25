"""
Webhook multi-sessão: roteamento pelo nome (prefixo do ambiente), espelho de
status da instância e o guard do SAIR contra mensagens de grupo.
"""
from types import SimpleNamespace

import pytest

from app.api.v1.routes.whatsapp import _tratar_mensagem, _tratar_status
from app.models.whatsapp_grupos import (
    INSTANCIA_CONECTADA, INSTANCIA_CRIADA, INSTANCIA_DESCONECTADA, INSTANCIA_REMOVIDA,
)


class _FakeInstancia(SimpleNamespace):
    pass


class _FakeRepoInstancias:
    def __init__(self, instancias):
        self.instancias = {i.nome_instancia: i for i in instancias}
        self.salvas = []

    def por_nome(self, nome):
        return self.instancias.get(nome)

    def salvar(self, instancia):
        self.salvas.append(instancia)
        return instancia


def _instancia(nome="mkdXXXXu1xabcd", status=INSTANCIA_CRIADA, numero=None):
    return _FakeInstancia(nome_instancia=nome, status=status, numero=numero,
                          falhas_seguidas=3, ultima_conexao_em=None)


@pytest.fixture
def ambiente(monkeypatch):
    """Prefixo do ambiente estável no teste + repo interceptado.

    O patch é na FONTE (instancia_service) — gerador de nome e roteador do
    webhook derivam do mesmo prefixo, e este teste garante que continuem."""
    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.identidade_do_banco",
        lambda: "XXXXrestodaref0000ab",
    )
    monkeypatch.setattr("app.api.v1.routes.whatsapp.settings.WAHA_SESSAO_RESUMO",
                        "mkd-resumo", raising=False)


def _com_repo(monkeypatch, repo):
    monkeypatch.setattr(
        "app.api.v1.routes.whatsapp.WhatsappInstanciaRepository",
        lambda db: repo,
    )


def test_working_marca_conectada_zera_disjuntor_e_captura_numero(ambiente, monkeypatch):
    inst = _instancia()
    repo = _FakeRepoInstancias([inst])
    _com_repo(monkeypatch, repo)

    _tratar_status(None, "mkdXXXXu1xabcd",
                   {"me": {"id": "5511999998888@c.us"}}, {"status": "WORKING"})

    assert inst.status == INSTANCIA_CONECTADA
    assert inst.falhas_seguidas == 0
    assert inst.numero == "5511999998888"
    assert repo.salvas   # espelho persistido


def test_queda_marca_desconectada(ambiente, monkeypatch):
    inst = _instancia(status=INSTANCIA_CONECTADA, numero="5511999998888")
    repo = _FakeRepoInstancias([inst])
    _com_repo(monkeypatch, repo)

    _tratar_status(None, "mkdXXXXu1xabcd", {}, {"status": "STOPPED"})
    assert inst.status == INSTANCIA_DESCONECTADA


def test_sessao_de_outro_ambiente_e_ignorada(ambiente, monkeypatch):
    # Prefixo de OUTRO banco no mesmo servidor WAHA: tratar seria fratricídio.
    inst = _instancia(nome="mkdYYYYu9xzzzz")
    repo = _FakeRepoInstancias([inst])
    _com_repo(monkeypatch, repo)

    _tratar_status(None, "mkdYYYYu9xzzzz", {}, {"status": "WORKING"})
    assert inst.status == INSTANCIA_CRIADA   # intocada
    assert repo.salvas == []


def test_sessao_do_resumo_nao_mexe_em_instancia(ambiente, monkeypatch):
    repo = _FakeRepoInstancias([])
    _com_repo(monkeypatch, repo)
    _tratar_status(None, "mkd-resumo", {}, {"status": "WORKING"})
    assert repo.salvas == []


def test_sair_em_mensagem_de_grupo_e_ignorado(ambiente, monkeypatch):
    # SAIR num grupo desligaria o resumo de quem por acaso está no grupo.
    chamadas = []
    monkeypatch.setattr("app.api.v1.routes.whatsapp._servico",
                        lambda db: chamadas.append("servico"))

    _tratar_mensagem(None, "mkd-resumo",
                     {"from": "120363123@g.us", "body": "SAIR", "fromMe": False})
    assert chamadas == []


def test_sair_de_dm_desliga_e_agradece(ambiente, monkeypatch):
    desligados = []

    class _Servico:
        def desligar_por_numero(self, numero):
            desligados.append(numero)
            return 1

    enviados = []

    class _Cliente:
        def enviar_texto(self, chat_id, texto):
            enviados.append(chat_id)

    monkeypatch.setattr("app.api.v1.routes.whatsapp._servico", lambda db: _Servico())
    monkeypatch.setattr("app.api.v1.routes.whatsapp._cliente_resumo", lambda: _Cliente())

    _tratar_mensagem(None, "mkd-resumo",
                     {"from": "5511999998888@c.us", "body": "sair", "fromMe": False})
    assert desligados == ["5511999998888"]
    assert enviados == ["5511999998888@c.us"]


def test_mensagem_em_sessao_de_aluna_nao_aciona_sair(ambiente, monkeypatch):
    # Sessões de aluna nem assinam `message`; se chegar, ignora.
    chamadas = []
    monkeypatch.setattr("app.api.v1.routes.whatsapp._servico",
                        lambda db: chamadas.append("servico"))
    _tratar_mensagem(None, "mkdXXXXu1xabcd",
                     {"from": "5511999998888@c.us", "body": "SAIR", "fromMe": False})
    assert chamadas == []


def test_nome_gerado_sempre_pertence_ao_proprio_ambiente(ambiente):
    """Se a convenção de nome mudar num lado só, o webhook passa a descartar
    TODOS os eventos em silêncio — este teste trava gerador e roteador juntos."""
    from app.services.whatsapp_instancia_service import (
        nome_de_instancia, pertence_a_este_ambiente,
    )

    assert pertence_a_este_ambiente(nome_de_instancia(42)) is True
    assert pertence_a_este_ambiente("mkdYYYYu42xabcd") is False


def test_evento_atrasado_nao_ressuscita_instancia_removida(ambiente, monkeypatch):
    # O retry do WAHA entrega o STOPPED do logout DEPOIS do remover; tratar
    # devolveria o número deletado à lista (e ao limite do plano).
    inst = _instancia(status=INSTANCIA_REMOVIDA)
    repo = _FakeRepoInstancias([inst])
    _com_repo(monkeypatch, repo)

    _tratar_status(None, "mkdXXXXu1xabcd", {}, {"status": "STOPPED"})
    assert inst.status == INSTANCIA_REMOVIDA
    assert repo.salvas == []
