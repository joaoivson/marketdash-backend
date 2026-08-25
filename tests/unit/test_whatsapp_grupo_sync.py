"""
Sync de grupos — as duas decisões que não podem regredir:

1. sub_id + custom_link nascem NO SYNC (atribuição perdida entre o sync e o
   primeiro envio seria irrecuperável);
2. grupo que some vira ativo=False, NUNCA é deletado.
"""
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.models.custom_link import CustomLink
from app.models.sync_run import SyncRun
from app.models.whatsapp_grupos import (
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.services.whatsapp_grupo_sync_service import (
    WhatsappGrupoSyncService, base36, sub_id_do_grupo,
)

USUARIA = 1


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for t in (CustomLink.__table__, WhatsappInstancia.__table__,
              WhatsappGrupo.__table__, WhatsappGrupoInstancia.__table__):
        t.create(engine)
    sessao = sessionmaker(bind=engine)()
    # sync_runs tem JSONB (que o SQLite não compila) — DDL manual equivalente.
    sessao.execute(text("""
        CREATE TABLE sync_runs (
            id INTEGER PRIMARY KEY,
            source VARCHAR, trigger VARCHAR, user_id INTEGER,
            days_back INTEGER, empty_attempt BOOLEAN,
            status VARCHAR, started_at TIMESTAMP, finished_at TIMESTAMP,
            records_fetched INTEGER, records_upserted INTEGER,
            is_suspected_partial BOOLEAN, error_message TEXT, details TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """))
    sessao.commit()
    yield sessao
    sessao.close()


class _FakeWaha:
    def __init__(self, paginas):
        self.paginas = paginas   # lista de listas (uma por offset)

    def sessao_info(self):
        return {"me": {"id": "5511999998888@c.us"}}

    def listar_grupos(self, limite=100, offset=0):
        indice = offset // 100
        return self.paginas[indice] if indice < len(self.paginas) else []

    def convite_do_grupo(self, jid):
        return f"https://chat.whatsapp.com/conv-{jid[:6]}"


def _instancia(db, numero="5511999998888"):
    inst = WhatsappInstancia(
        user_id=USUARIA, nome_instancia="mkdtestu1xabcd",
        numero=numero, status="conectada",
    )
    db.add(inst)
    db.commit()
    return inst


def _grupo(jid="120363000000000001@g.us", nome="Achadinhos", admin=True, announce=False):
    participantes = [
        {"id": "5511999998888@c.us", "role": "admin" if admin else "participant"},
        {"id": "5521888887777@c.us", "role": "participant"},
    ]
    return {"id": jid, "subject": nome, "participants": participantes,
            "announce": announce}


def test_base36_estavel():
    assert base36(0) == "0"
    assert base36(35) == "z"
    assert base36(36) == "10"
    assert sub_id_do_grupo(123) == "wg3f"


def test_grupo_novo_nasce_com_sub_id_e_custom_link(db):
    inst = _instancia(db)
    svc = WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]]))
    r = svc.sincronizar(inst)

    assert r == {"vistos": 1, "novos": 1, "atualizados": 0, "desativados": 0}
    grupo = db.query(WhatsappGrupo).one()
    assert grupo.sub_id == sub_id_do_grupo(grupo.id)
    assert grupo.participantes == 2
    assert grupo.sou_admin is True
    assert grupo.permite_envio is True
    assert grupo.link_convite and grupo.link_convite.startswith("https://chat.whatsapp.com/")

    link = db.query(CustomLink).one()
    assert grupo.custom_link_id == link.id
    assert link.tag == "whatsapp"   # fora do limite do plano, fora de Meus Links


def test_membro_comum_de_grupo_so_admin_nao_permite_envio(db):
    inst = _instancia(db)
    dados = _grupo(admin=False, announce=True)   # "só admins podem enviar" ligado
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[dados]])).sincronizar(inst)

    grupo = db.query(WhatsappGrupo).one()
    assert grupo.sou_admin is False
    assert grupo.permite_envio is False


def test_grupo_que_some_desativa_sem_deletar(db):
    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)
    # Segundo sync: o grupo sumiu do retorno.
    r = WhatsappGrupoSyncService(db, cliente=_FakeWaha([[]])).sincronizar(inst)

    assert r["desativados"] == 1
    grupo = db.query(WhatsappGrupo).one()   # a linha continua existindo
    assert grupo.ativo is False
    assert grupo.sub_id is not None         # atribuição histórica preservada


def test_grupo_que_reaparece_revive_com_o_mesmo_sub_id(db):
    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)
    sub_id_original = db.query(WhatsappGrupo).one().sub_id
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[]])).sincronizar(inst)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)

    grupo = db.query(WhatsappGrupo).one()
    assert grupo.ativo is True
    assert grupo.sub_id == sub_id_original
    assert db.query(CustomLink).count() == 1   # não cria segundo link


def test_mesmo_grupo_em_duas_instancias_e_n_para_n(db):
    inst_a = _instancia(db)
    inst_b = WhatsappInstancia(user_id=USUARIA, nome_instancia="mkdtestu1xefgh",
                               numero="5511888887777", status="conectada")
    db.add(inst_b)
    db.commit()

    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst_a)
    # O segundo número é membro comum do MESMO grupo.
    dados_b = _grupo(admin=False)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[dados_b]])).sincronizar(inst_b)

    assert db.query(WhatsappGrupo).count() == 1   # UNIQUE(user_id, jid)
    vinculos = db.query(WhatsappGrupoInstancia).all()
    assert {(v.instancia_id, v.sou_admin) for v in vinculos} == {
        (inst_a.id, True), (inst_b.id, False),
    }


def test_sync_grava_sync_run_para_o_painel_admin(db):
    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)

    run = db.query(SyncRun).one()
    assert run.source == "whatsapp_grupos"
    assert run.status == "success"
    assert run.records_fetched == 1


def test_payload_sem_flags_de_anuncio_nao_assume_grupo_aberto(db):
    inst = _instancia(db)
    dados = {"id": "120363000000000002@g.us", "subject": "Sem flags",
             "participants": [{"id": "5511999998888@c.us", "role": "participant"}]}
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[dados]])).sincronizar(inst)

    grupo = db.query(WhatsappGrupo).one()
    # Membro comum + payload omisso: marcar "aberto" no chute mandaria o lote
    # contra grupos que devolvem 403.
    assert grupo.permite_envio is False


def test_link_de_grupo_fica_fora_de_meus_links_pela_fk_nao_pela_tag(db):
    from app.repositories.custom_link_repository import CustomLinkRepository

    inst = _instancia(db)
    WhatsappGrupoSyncService(db, cliente=_FakeWaha([[_grupo()]])).sincronizar(inst)
    # A usuária cria um link PESSOAL com a tag "whatsapp" — precisa continuar
    # visível e contando no limite (tag é texto livre, não marcador interno).
    pessoal = CustomLink(user_id=USUARIA, name="meu link", original_url="https://x",
                         slug="pessoal1", tag="whatsapp", is_active=True)
    db.add(pessoal)
    db.commit()

    ids_grupo = CustomLinkRepository(db).ids_de_links_de_grupo(USUARIA)
    grupo = db.query(WhatsappGrupo).one()
    assert ids_grupo == {grupo.custom_link_id}
    assert pessoal.id not in ids_grupo
