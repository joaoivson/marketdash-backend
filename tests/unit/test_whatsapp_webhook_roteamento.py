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


def _instancia(nome="mkdXXXXu1xabcd", status=INSTANCIA_CRIADA, numero=None,
               user_id=1):
    return _FakeInstancia(nome_instancia=nome, status=status, numero=numero,
                          falhas_seguidas=3, ultima_conexao_em=None,
                          user_id=user_id, id=1)


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


# --- F8: mensagem de grupo em sessão de aluna -------------------------------


class _DbFalso:
    """Só o suficiente para provar o que o handler faz ANTES de tocar no banco."""
    def __init__(self, grupo=None):
        self.grupo = grupo
        self.consultas = 0
        self.commits = 0
        self.rollbacks = 0

    def query(self, *_a, **_k):
        self.consultas += 1
        return self

    def filter(self, *_a, **_k):
        return self

    def first(self):
        return self.grupo

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _payload_de_grupo(texto="Oferta https://shopee.com.br/x",
                      de="120363000000000001@g.us", from_me=False):
    return {"from": de, "body": texto, "fromMe": from_me}


def test_conversa_privada_em_sessao_de_aluna_nao_entra_no_monitoramento(
        ambiente, monkeypatch):
    """Monitoramento é de GRUPO. Mensagem direta no número da afiliada não pode
    sequer chegar a consultar o banco."""
    repo = _FakeRepoInstancias([_instancia()])
    _com_repo(monkeypatch, repo)
    db = _DbFalso()

    _tratar_mensagem(db, "mkdXXXXu1xabcd", _payload_de_grupo(de="5511999@c.us"))
    assert db.consultas == 0


def test_mensagem_propria_nao_e_capturada(ambiente, monkeypatch):
    """Sem isso, o próprio envio da afiliada voltaria como captura e ela
    replicaria a si mesma em loop."""
    repo = _FakeRepoInstancias([_instancia()])
    _com_repo(monkeypatch, repo)
    db = _DbFalso()

    _tratar_mensagem(db, "mkdXXXXu1xabcd", _payload_de_grupo(from_me=True))
    assert db.consultas == 0


def test_mensagem_de_grupo_vazia_e_ignorada(ambiente, monkeypatch):
    """Mídia sem legenda não tem o que replicar."""
    repo = _FakeRepoInstancias([_instancia()])
    _com_repo(monkeypatch, repo)
    db = _DbFalso()

    _tratar_mensagem(db, "mkdXXXXu1xabcd", _payload_de_grupo(texto="   "))
    assert db.consultas == 0


def test_sessao_de_outro_ambiente_nao_captura(ambiente, monkeypatch):
    """hml e produção podem dividir o mesmo servidor WAHA."""
    repo = _FakeRepoInstancias([_instancia()])
    _com_repo(monkeypatch, repo)
    db = _DbFalso()

    _tratar_mensagem(db, "mkdZZZZu1xabcd", _payload_de_grupo())
    assert db.consultas == 0


def test_grupo_desconhecido_nao_captura(ambiente, monkeypatch):
    """Grupo que não é da usuária desta sessão: nada a fazer, e nada gravado."""
    repo = _FakeRepoInstancias([_instancia()])
    _com_repo(monkeypatch, repo)
    db = _DbFalso(grupo=None)

    _tratar_mensagem(db, "mkdXXXXu1xabcd", _payload_de_grupo())
    assert db.commits == 0


# --- leitura tolerante do evento de participantes ----------------------------
#
# O schema documentado deste evento é camelCase (`payload.group.id`,
# `payload.participants[].id`) — mas a doc do REST `/groups` também era, e o
# engine GOWS devolvia PascalCase. Aquele engano zerou o sync em 26/08 sem
# nenhum erro. Aqui o preço de errar é o mesmo: nenhuma entrada ou saída
# registrada, em silêncio, e a F6 inteira sem dado.

@pytest.mark.parametrize("payload,esperado_jid,esperado_participantes", [
    # forma documentada
    ({"type": "join", "group": {"id": "120363000000000001@g.us"},
      "participants": [{"id": "5511999998888@c.us", "role": "participant"}]},
     "120363000000000001@g.us", ["5511999998888@c.us"]),
    # PascalCase, como o GOWS faz no REST
    ({"type": "join", "group": {"JID": "120363000000000001@g.us"},
      "participants": [{"JID": "5511999998888@s.whatsapp.net"}]},
     "120363000000000001@g.us", ["5511999998888@s.whatsapp.net"]),
    # endereçamento LID: sem `id`, só telefone
    ({"type": "leave", "group": {"id": "120363000000000001@g.us"},
      "participants": [{"PhoneNumber": "5511999998888@s.whatsapp.net"}]},
     "120363000000000001@g.us", ["5511999998888@s.whatsapp.net"]),
])
def test_participantes_sao_lidos_em_qualquer_caixa(
    ambiente, monkeypatch, payload, esperado_jid, esperado_participantes
):
    from app.api.v1.routes import whatsapp as rota

    repo = _FakeRepoInstancias([_instancia()])
    _com_repo(monkeypatch, repo)

    capturado = {}

    class _ServicoFake:
        def __init__(self, db):
            pass

        def registrar(self, user_id, grupo_jid, acao, participantes):
            capturado.update(grupo=grupo_jid, acao=acao, participantes=participantes)
            return len(participantes)

    monkeypatch.setattr(
        "app.services.grupo_evento_service.GrupoEventoService", _ServicoFake
    )
    rota._tratar_participantes(None, "mkdXXXXu1xabcd", payload)

    assert capturado["grupo"] == esperado_jid
    assert capturado["participantes"] == esperado_participantes


def test_participante_sem_identificador_nenhum_nao_vira_string_vazia(
    ambiente, monkeypatch
):
    """Hash de string vazia seria um pseudônimo colidindo entre todo mundo."""
    from app.api.v1.routes import whatsapp as rota

    _com_repo(monkeypatch, _FakeRepoInstancias([_instancia()]))
    chamou = []

    class _ServicoFake:
        def __init__(self, db):
            pass

        def registrar(self, *a):
            chamou.append(a)
            return 0

    monkeypatch.setattr(
        "app.services.grupo_evento_service.GrupoEventoService", _ServicoFake
    )
    rota._tratar_participantes(None, "mkdXXXXu1xabcd", {
        "type": "join", "group": {"id": "120363000000000001@g.us"},
        "participants": [{"algumCampoNovo": "x"}],
    })
    assert chamou == []   # nada a registrar, e nada de hash vazio
