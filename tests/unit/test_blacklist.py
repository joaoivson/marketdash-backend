"""
Blacklist de números (item 17 da spec).

A tabela nasceu na 060 e ficou INERTE: sem service, rota, tela, e ninguém a lia
no envio. Estes testes travam o que ela precisa fazer para valer alguma coisa.

Invariantes:
  * o número NÃO é guardado em claro — só HMAC + máscara;
  * bloqueado não recebe o resumo diário, mesmo com opt-in confirmado;
  * bloqueado que entra num grupo é removido, e SÓ quando somos admin;
  * a lista de uma afiliada não enxerga a da outra.
"""
import uuid

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = "postgresql://dashads_user:dashads_password@localhost:5434/dashads_db"
try:
    _p = create_engine(PG_URL, pool_pre_ping=True)
    with _p.connect() as _c:
        _c.execute(text("SELECT 1"))
    PG_OK = True
except Exception:
    PG_OK = False

pytestmark = pytest.mark.skipif(not PG_OK, reason="Postgres local (5434) indisponível")

if PG_OK:
    from app.db.base import Base
    import app.models  # noqa: F401
    ENGINE = create_engine(PG_URL)
    Base.metadata.create_all(ENGINE)
    with ENGINE.begin() as _conn:
        # create_all não altera tabela existente — espelho do gotcha de produção.
        _conn.execute(text("ALTER TABLE blacklist_numeros ADD COLUMN IF NOT EXISTS "
                           "numero_mascarado VARCHAR(24)"))
        _conn.execute(text("ALTER TABLE blacklist_numeros ADD COLUMN IF NOT EXISTS "
                           "remover_dos_grupos BOOLEAN NOT NULL DEFAULT TRUE"))
    Sessao = sessionmaker(bind=ENGINE)

from app.models.roteiro import BlacklistNumero  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.blacklist_service import (  # noqa: E402
    BlacklistService, NumeroInvalido, hash_do_numero, mascarar, numero_de_jid,
)

NUMERO = "(11) 98765-4321"
E164 = "5511987654321"


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _usuaria(db):
    u = User(email=f"bl-{uuid.uuid4().hex[:8]}@x.com", hashed_password="x")
    db.add(u); db.flush()
    return u


# --- o número não fica guardado ---------------------------------------------


def test_numero_nao_e_persistido_em_claro(db):
    """É a promessa que separa "lista de bloqueio" de "lista de telefones"."""
    user = _usuaria(db)
    item = BlacklistService(db).adicionar(user.id, NUMERO, "pediu para sair")
    db.commit()

    colunas = {c.name for c in BlacklistNumero.__table__.columns}
    assert "numero" not in colunas
    for valor in (item.numero_hash, item.numero_mascarado or "", item.motivo or ""):
        assert E164 not in valor
    assert item.numero_mascarado == "+55 11 ****-4321"


def test_mascara_reconhece_sem_revelar():
    assert mascarar(E164) == "+55 11 ****-4321"
    assert E164 not in mascarar(E164)


def test_formatos_diferentes_do_mesmo_numero_dao_o_mesmo_hash():
    """Ela digita de um jeito; o webhook entrega de outro. Se não normalizasse,
    a lista nunca casaria com quem entra no grupo."""
    a = hash_do_numero("5511987654321")
    assert a == hash_do_numero(numero_de_jid("5511987654321@c.us"))


def test_numero_invalido_recusa(db):
    user = _usuaria(db)
    with pytest.raises(NumeroInvalido):
        BlacklistService(db).adicionar(user.id, "abc")


def test_repetir_o_numero_atualiza_em_vez_de_duplicar(db):
    """Devolver erro faria ela apagar e recriar só para mudar uma palavra."""
    user = _usuaria(db)
    svc = BlacklistService(db)
    svc.adicionar(user.id, NUMERO, "motivo antigo", remover_dos_grupos=True)
    db.commit()
    svc.adicionar(user.id, NUMERO, "motivo novo", remover_dos_grupos=False)
    db.commit()

    itens = svc.listar(user.id)
    assert len(itens) == 1
    assert itens[0].motivo == "motivo novo"
    assert itens[0].remover_dos_grupos is False


# --- isolamento -------------------------------------------------------------


def test_lista_de_uma_afiliada_nao_enxerga_a_da_outra(db):
    minha, outra = _usuaria(db), _usuaria(db)
    svc = BlacklistService(db)
    svc.adicionar(minha.id, NUMERO)
    svc.adicionar(outra.id, "(21) 98888-7777")
    db.commit()

    assert len(svc.listar(minha.id)) == 1
    assert svc.bloqueado(minha.id, "5521988887777") is None
    assert svc.bloqueado(outra.id, E164) is None


def test_remover_item_de_outra_afiliada_nao_apaga_nada(db):
    minha, outra = _usuaria(db), _usuaria(db)
    svc = BlacklistService(db)
    alheio = svc.adicionar(outra.id, NUMERO)
    db.commit()

    assert svc.remover(minha.id, alheio.id) is False
    db.commit()
    assert len(svc.listar(outra.id)) == 1


# --- consulta em lote (gancho das menções) ----------------------------------


def test_consulta_em_lote_devolve_so_os_bloqueados(db):
    user = _usuaria(db)
    svc = BlacklistService(db)
    svc.adicionar(user.id, NUMERO)
    db.commit()

    achados = svc.bloqueados_entre(user.id, [E164, "5511900000000", ""])
    assert achados == {E164}
    assert svc.bloqueados_entre(user.id, []) == set()


# --- efeito 1: resumo diário -------------------------------------------------


def test_bloqueado_nao_recebe_o_resumo_mesmo_com_optin_confirmado(db):
    """
    Blacklist manda mais que opt-in: "não quero mais nada de você" é um pedido
    mais forte do que uma preferência ligada em algum momento — e é a lista que
    ela usa como prova se alguém reclamar.
    """
    from types import SimpleNamespace

    from app.models.whatsapp import ENVIO_OK, STATUS_CONFIRMADO, TIPO_RESUMO
    from app.services.whatsapp_envio_service import WhatsappEnvioService

    user = _usuaria(db)
    BlacklistService(db).adicionar(user.id, NUMERO)
    db.commit()

    optin = SimpleNamespace(user_id=user.id, numero=E164, status=STATUS_CONFIRMADO)

    class _Repo:
        def __init__(self):
            self.envios = []

        def confirmados_dos_planos(self, planos):
            return [(optin, "max")]

        def ja_enviou(self, *a, **k):
            return False

        def registrar_envio(self, *a, **k):
            self.envios.append(a)
            return True

        def enviados_no_dia(self, dia):
            return 0

    class _Cliente:
        def __init__(self):
            self.enviadas = []

        def configurado(self):
            return True

        def conectado(self):
            return True

        def enviar_texto(self, chat_id, texto):
            self.enviadas.append(chat_id)
            return {"ok": True}

    repo, cliente = _Repo(), _Cliente()
    servico = WhatsappEnvioService(
        db=db, repo=repo, cliente=cliente,
        buscar_usuario=lambda uid: SimpleNamespace(name="Maria"),
        dormir=lambda s: None,
    )
    servico.resumo_svc = SimpleNamespace(
        montar=lambda uid, nome, dia: SimpleNamespace(texto="resumo", tem_movimento=True)
    )
    r = servico.enviar_lote()

    assert cliente.enviadas == [], "mensagem saiu para um número bloqueado"
    assert r.enviados == 0
    assert r.pulados == 1


# --- efeito 2: entrada em grupo ---------------------------------------------


def _grupo(db, user, sou_admin):
    from app.models.whatsapp_grupos import (
        WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
    )
    suf = uuid.uuid4().hex[:8]
    inst = WhatsappInstancia(user_id=user.id, nome_instancia=f"mkdbl{suf}",
                             status="conectada")
    db.add(inst); db.flush()
    g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}@g.us", nome="G",
                      ativo=True, permite_envio=True, sou_admin=sou_admin,
                      participantes=10, capacidade=1024, sub_id=f"wg{suf}")
    db.add(g); db.flush()
    db.add(WhatsappGrupoInstancia(grupo_id=g.id, instancia_id=inst.id))
    db.commit()
    return g


def _capturar_remocoes(monkeypatch):
    removidos = []

    class _Cliente:
        def remover_participante(self, jid_grupo, jid_participante):
            removidos.append((jid_grupo, jid_participante))

    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.cliente_da_sessao",
        lambda nome: _Cliente(),
    )
    return removidos


def test_bloqueado_que_entra_no_grupo_e_removido(db, monkeypatch):
    from app.services.grupo_evento_service import GrupoEventoService

    user = _usuaria(db)
    grupo = _grupo(db, user, sou_admin=True)
    BlacklistService(db).adicionar(user.id, NUMERO, remover_dos_grupos=True)
    db.commit()
    removidos = _capturar_remocoes(monkeypatch)

    GrupoEventoService(db).registrar(user.id, grupo.jid, "join",
                                     [f"{E164}@c.us", "5511900000000@c.us"])
    assert removidos == [(grupo.jid, f"{E164}@c.us")]


def test_sem_ser_admin_nao_tenta_remover(db, monkeypatch):
    """Sem isso o WAHA devolve 403 a cada entrada e o log enche de um erro que
    não é erro."""
    from app.services.grupo_evento_service import GrupoEventoService

    user = _usuaria(db)
    grupo = _grupo(db, user, sou_admin=False)
    BlacklistService(db).adicionar(user.id, NUMERO, remover_dos_grupos=True)
    db.commit()
    removidos = _capturar_remocoes(monkeypatch)

    GrupoEventoService(db).registrar(user.id, grupo.jid, "join", [f"{E164}@c.us"])
    assert removidos == []


def test_bloqueio_sem_remocao_nao_expulsa(db, monkeypatch):
    """"Não quero que receba" e "quero fora dos meus grupos" são pedidos
    diferentes — por isso a escolha é por entrada."""
    from app.services.grupo_evento_service import GrupoEventoService

    user = _usuaria(db)
    grupo = _grupo(db, user, sou_admin=True)
    BlacklistService(db).adicionar(user.id, NUMERO, remover_dos_grupos=False)
    db.commit()
    removidos = _capturar_remocoes(monkeypatch)

    GrupoEventoService(db).registrar(user.id, grupo.jid, "join", [f"{E164}@c.us"])
    assert removidos == []


def test_entrada_fica_registrada_mesmo_se_a_remocao_falhar(db, monkeypatch):
    """
    A remoção roda DEPOIS do commit de propósito: a entrada aconteceu e tem que
    constar no histórico. Apagar o rastro deixaria evasão e "entraram e ficaram"
    mentindo.
    """
    from app.models.campanha_link import GrupoEvento
    from app.services.grupo_evento_service import GrupoEventoService
    from app.services.waha_client import ErroWhatsapp

    user = _usuaria(db)
    grupo = _grupo(db, user, sou_admin=True)
    BlacklistService(db).adicionar(user.id, NUMERO)
    db.commit()

    class _ClienteQuebrado:
        def remover_participante(self, *a, **k):
            raise ErroWhatsapp("sem_permissao", "não sou admin")

    monkeypatch.setattr(
        "app.services.whatsapp_instancia_service.cliente_da_sessao",
        lambda nome: _ClienteQuebrado(),
    )
    gravados = GrupoEventoService(db).registrar(user.id, grupo.jid, "join",
                                                [f"{E164}@c.us"])
    assert gravados == 1
    assert db.query(GrupoEvento).filter(GrupoEvento.grupo_id == grupo.id).count() == 1
