"""
Link de entrada (F6): roteamento sequencial/aleatório, lotação, abertura
automática, preview fora da métrica e os eventos que sustentam a evasão.

Postgres real: o roteamento usa FOR UPDATE SKIP LOCKED, que o SQLite não tem.
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

PG_URL = "postgresql://dashads_user:dashads_password@localhost:5434/dashads_db"
try:
    _probe = create_engine(PG_URL, pool_pre_ping=True)
    with _probe.connect() as _c:
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
        # 074 (toggle "Ativo" da usuária): create_all NÃO adiciona coluna em
        # tabela existente — o mesmo gotcha de produção, no banco de teste.
        _conn.execute(text(
            "ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS "
            "ativado BOOLEAN NOT NULL DEFAULT FALSE"
        ))
    Sessao = sessionmaker(bind=ENGINE)

from app.models.campanha_grupos import Campanha, CampanhaGrupo  # noqa: E402
from app.models.campanha_link import (  # noqa: E402
    EVENTO_ENTRADA, EVENTO_SAIDA, ORIGEM_LINK, ORIGEM_ORGANICA, CampanhaLink,
    CampanhaLinkEvento, GrupoEvento,
)
from app.models.user import User  # noqa: E402
from app.models.whatsapp_grupos import WhatsappGrupo  # noqa: E402
from app.repositories.campanha_link_repository import CampanhaLinkRepository  # noqa: E402
from app.services.campanha_link_service import (  # noqa: E402
    CampanhaLinkService, LinkInvalido, SemVaga,
)
from app.services.grupo_evento_service import GrupoEventoService, identificador  # noqa: E402


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _cenario(db, grupos=2, capacidade=1024, participantes=0, aleatoria=False,
             abertura_automatica=True, abertos=None):
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"g-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    campanha = Campanha(user_id=user.id, nome=f"c-{suf}",
                        estrategia_entrada="aleatoria" if aleatoria else "sequencial",
                        abertura_automatica=abertura_automatica)
    db.add(campanha); db.flush()
    criados = []
    for i in range(grupos):
        # `ativado=True`: monitoramento/eventos são só de grupo que a usuária
        # ligou (spec §6.2) — sem o toggle, registrar() ignora tudo.
        g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}{i}@g.us", nome=f"G{i}",
                          ativo=True, ativado=True, permite_envio=True,
                          participantes=participantes, capacidade=capacidade,
                          link_convite=f"https://chat.whatsapp.com/{suf}{i}")
        db.add(g); db.flush()
        aberto = True if abertos is None else (i in abertos)
        db.add(CampanhaGrupo(campanha_id=campanha.id, grupo_id=g.id, posicao=i,
                             aberto=aberto))
        criados.append(g)
    db.commit()
    link = CampanhaLinkService(db).obter_ou_criar(campanha)
    return user, campanha, criados, link


def _rotear(db, slug, ip="1.2.3.4", preview=False):
    return CampanhaLinkService(db).rotear(
        slug, ip=ip, user_agent="Mozilla/5.0 (iPhone)", referer=None,
        is_preview=preview,
    )


def test_sequencial_enche_o_primeiro_antes_do_segundo(db):
    user, campanha, grupos, link = _cenario(db)
    for i in range(3):
        _l, convite = _rotear(db, link.slug, ip=f"9.9.9.{i}")
        assert convite == grupos[0].link_convite   # sempre a posição 1


def test_grupo_lotado_sai_da_rotacao(db):
    user, campanha, grupos, link = _cenario(db, capacidade=10)
    grupos[0].participantes = 10        # lotou
    db.add(grupos[0]); db.commit()

    _l, convite = _rotear(db, link.slug)
    assert convite == grupos[1].link_convite


def test_abertura_automatica_abre_o_proximo_quando_o_atual_lota(db):
    # só o grupo 0 aberto; ele está cheio → o 1 é aberto na hora
    user, campanha, grupos, link = _cenario(db, capacidade=5, abertos={0})
    grupos[0].participantes = 5
    db.add(grupos[0]); db.commit()

    _l, convite = _rotear(db, link.slug)
    assert convite == grupos[1].link_convite
    vinculo = (db.query(CampanhaGrupo)
               .filter(CampanhaGrupo.grupo_id == grupos[1].id).one())
    assert vinculo.aberto is True


def test_sem_abertura_automatica_e_tudo_cheio_da_vagas_esgotadas(db):
    user, campanha, grupos, link = _cenario(db, capacidade=5, abertura_automatica=False,
                                            abertos={0})
    grupos[0].participantes = 5
    db.add(grupos[0]); db.commit()
    with pytest.raises(SemVaga):
        _rotear(db, link.slug)


def test_aleatoria_distribui_entre_os_abertos(db):
    user, campanha, grupos, link = _cenario(db, grupos=3, aleatoria=True)
    destinos = {_rotear(db, link.slug, ip=f"8.8.8.{i}")[1] for i in range(25)}
    assert len(destinos) > 1     # não concentra num só


def test_preview_roteia_mas_nao_conta_como_clique_real(db):
    user, campanha, grupos, link = _cenario(db)
    _rotear(db, link.slug, preview=True)

    eventos = db.query(CampanhaLinkEvento).filter(
        CampanhaLinkEvento.link_id == link.id).all()
    assert len(eventos) == 1 and eventos[0].is_teste is True
    # métrica ignora teste
    assert CampanhaLinkRepository(db).cliques_por_grupo(link.id) == {}


def test_bot_nao_conta_como_pessoa(db):
    user, campanha, grupos, link = _cenario(db)
    CampanhaLinkService(db).rotear(
        link.slug, ip="5.5.5.5",
        user_agent="facebookexternalhit/1.1", referer=None, is_preview=False,
    )
    assert db.query(CampanhaLinkEvento).filter(
        CampanhaLinkEvento.link_id == link.id).count() == 0


def test_link_inativo_ou_inexistente(db):
    user, campanha, grupos, link = _cenario(db)
    with pytest.raises(LinkInvalido):
        _rotear(db, "naoexiste")
    link.ativo = False
    db.add(link); db.commit()
    with pytest.raises(LinkInvalido):
        _rotear(db, link.slug)


def test_numero_cru_nunca_vira_registro_de_evento(db):
    jid = "5511999998888@c.us"
    assert identificador(jid) != jid
    assert len(identificador(jid)) == 64


def test_entrada_logo_apos_o_clique_e_atribuida_ao_link(db):
    user, campanha, grupos, link = _cenario(db)
    _rotear(db, link.slug)                       # clique roteado ao grupo 0

    n = GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                         ["5511999990001@c.us"])
    evento = db.query(GrupoEvento).filter(GrupoEvento.grupo_id == grupos[0].id).one()
    assert n == 1
    assert evento.origem == ORIGEM_LINK and evento.link_evento_id is not None
    db.refresh(grupos[0])
    assert grupos[0].participantes == 1          # contador anda na hora


def test_entrada_sem_clique_recente_e_organica(db):
    user, campanha, grupos, link = _cenario(db)
    GrupoEventoService(db).registrar(user.id, grupos[0].jid, "join",
                                     ["5511999990002@c.us"])
    evento = db.query(GrupoEvento).filter(GrupoEvento.grupo_id == grupos[0].id).one()
    assert evento.origem == ORIGEM_ORGANICA and evento.link_evento_id is None


def test_promote_demote_nao_viram_entrada_nem_saida(db):
    user, campanha, grupos, link = _cenario(db)
    svc = GrupoEventoService(db)
    assert svc.registrar(user.id, grupos[0].jid, "promote", ["5511999990003@c.us"]) == 0
    assert db.query(GrupoEvento).filter(GrupoEvento.grupo_id == grupos[0].id).count() == 0


def test_entraram_e_ficaram_desconta_quem_saiu(db):
    user, campanha, grupos, link = _cenario(db)
    svc = GrupoEventoService(db)
    a, b, c = "5511999990001@c.us", "5511999990002@c.us", "5511999990003@c.us"
    svc.registrar(user.id, grupos[0].jid, "join", [a, b, c])
    svc.registrar(user.id, grupos[0].jid, "leave", [b])

    metricas = CampanhaLinkRepository(db).eventos_por_grupo([grupos[0].id])[grupos[0].id]
    assert metricas["entradas"] == 3
    assert metricas["saidas"] == 1
    assert metricas["ficaram"] == 2      # a e c
    db.refresh(grupos[0])
    assert grupos[0].participantes == 2


def test_quem_saiu_e_voltou_conta_como_ficou(db):
    user, campanha, grupos, link = _cenario(db)
    svc = GrupoEventoService(db)
    p = "5511999990009@c.us"
    svc.registrar(user.id, grupos[0].jid, "join", [p])
    svc.registrar(user.id, grupos[0].jid, "leave", [p])
    svc.registrar(user.id, grupos[0].jid, "join", [p])

    m = CampanhaLinkRepository(db).eventos_por_grupo([grupos[0].id])[grupos[0].id]
    assert m["ficaram"] == 1     # a última entrada não tem saída depois


def test_evento_de_grupo_desconhecido_e_ignorado(db):
    user, campanha, grupos, link = _cenario(db)
    n = GrupoEventoService(db).registrar(user.id, "120363000000@g.us", "join",
                                         ["5511999990004@c.us"])
    assert n == 0
    assert db.query(GrupoEvento).filter(
        GrupoEvento.grupo_id.in_([g.id for g in grupos])).count() == 0
