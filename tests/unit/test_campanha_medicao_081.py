"""
Rodada de medição das campanhas de grupos (documento de 05/09, migration 081).

Os invariantes desta rodada:

  * **evasão nunca passa de 100%** — a base é a população exposta ao risco de
    sair, não as entradas do período. Um grupo cheio (1 entrada, 9 saídas) dava
    900%;
  * **"vagas esgotadas" é exceção**, não o caminho do grupo cheio: o link manda
    para o primeiro da ordem e CONTA o clique (o teste vive em
    `test_link_de_entrada.py`, junto do resto da rotação);
  * **telefone brasileiro sai com 13 dígitos** — 1.752 de 2.499 vinham sem o 9;
  * **o Sub ID do grupo é legível e NUNCA rederivado** — renomear o grupo não
    pode mexer nele, sob pena de perder a atribuição de comissão;
  * **a atividade pagina por keyset** — `criado_em` empata em lote e OFFSET
    repete linha.
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
    Sessao = sessionmaker(bind=ENGINE)

from app.models.campanha_grupos import Campanha, CampanhaGrupo  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.whatsapp_grupos import WhatsappGrupo  # noqa: E402


@pytest.fixture
def db():
    s = Sessao()
    yield s
    s.rollback()
    s.close()


def _grupo(db, nome="Promos da Beatriz 💖🛍️ #1", participantes=0):
    suf = uuid.uuid4().hex[:8]
    user = User(email=f"m81-{suf}@x.com", hashed_password="x")
    db.add(user); db.flush()
    g = WhatsappGrupo(user_id=user.id, jid=f"12036{suf}@g.us", nome=nome,
                      ativo=True, ativado=True, participantes=participantes,
                      capacidade=1024, link_convite="https://chat.whatsapp.com/x")
    db.add(g); db.flush()
    return user, g


# --- evasão -----------------------------------------------------------------


def test_evasao_nunca_passa_de_100_por_cento():
    """
    O caso reportado: grupo #1 com 1 entrada e 9 saídas dava **900%**.

    A fórmula antiga dividia pelas ENTRADAS do período, e explodia justamente
    no caso mais comum — grupo cheio, que quase não recebe e continua perdendo
    gente. A base agora é `participantes + saidas`: todo mundo que esteve
    dentro em algum momento da janela, o que garante saídas <= base.
    """
    from app.services.campanha_resultado_service import _evasao

    # 9 saídas com 1 pessoa restando: 9 de 10 que estavam lá.
    assert _evasao(9, 1) == 90.0
    # Grupo que esvaziou por completo: 100%, nunca mais que isso.
    assert _evasao(9, 0) == 100.0
    # Caso normal de campanha grande.
    assert _evasao(45, 1601) == pytest.approx(2.73, abs=0.01)
    # Ninguém exposto: a métrica NÃO existe — 0,0 afirmaria "ninguém saiu".
    assert _evasao(0, 0) is None


# --- telefone ---------------------------------------------------------------


@pytest.mark.parametrize("entrada,esperado", [
    # 12 dígitos, celular (assinante 6-9): ganha o 9. É o caso de 1.752 dos
    # 2.499 números brasileiros medidos em homologação.
    ("553188887777", "5531988887777"),
    ("551188887777", "5511988887777"),   # DDD 11 também
    # já correto: intacto
    ("5531988887777", "5531988887777"),
    # fixo (assinante 2-5): NÃO leva 9 — inserir produziria número inexistente
    ("553128888777", "553128888777"),
    # estrangeiro: passa intacto (medidos: 5 na base)
    ("12125551234", "12125551234"),
    ("", ""),
])
def test_normalizacao_de_celular_brasileiro(entrada, esperado):
    from app.services.waha_client import normalizar_celular_br

    assert normalizar_celular_br(entrada) == esperado


def test_normalizacao_e_idempotente():
    """Roda no sync E no export — aplicar duas vezes não pode dobrar o 9."""
    from app.services.waha_client import normalizar_celular_br

    uma = normalizar_celular_br("553188887777")
    assert normalizar_celular_br(uma) == uma


# --- Sub ID legível ---------------------------------------------------------


def test_sub_id_do_grupo_e_legivel_e_sanitizado(db):
    """`wgea` não diz nada, e a afiliada vê esse código no relatório da própria
    Shopee, sem nome de grupo do lado."""
    import re

    from app.services.whatsapp_grupo_service import garantir_atribuicao

    user, g = _grupo(db, nome="Promos da Beatriz 💖🛍️ #1")
    garantir_atribuicao(db, g)
    db.flush()

    assert g.sub_id.startswith("grupo")
    assert "promosdabeatriz" in g.sub_id
    # Só [a-z0-9]: emoji, acento e espaço saem — a Shopee exige alfanumérico.
    assert re.fullmatch(r"grupo[a-z0-9]+", g.sub_id), g.sub_id
    assert len(g.sub_id) <= 64


def test_sub_id_NUNCA_e_rederivado_ao_renomear_o_grupo(db):
    """A trava que importa: rederivar quebraria a ligação com toda a comissão
    já atribuída, e em silêncio — o Sub ID simplesmente pararia de casar com o
    que a Shopee reporta."""
    from app.services.whatsapp_grupo_service import garantir_atribuicao

    user, g = _grupo(db, nome="Achadinhos da Bia")
    garantir_atribuicao(db, g)
    db.flush()
    original = g.sub_id

    g.nome = "Outro nome completamente diferente"
    db.add(g); db.flush()
    garantir_atribuicao(db, g)
    db.flush()

    assert g.sub_id == original


def test_grupo_sem_nome_utilizavel_cai_no_id_e_nao_em_string_vazia(db):
    """Nome só com emoji sanitiza para vazio. Sem o fallback, todo grupo assim
    nasceria como "grupo"+sufixo — colisão de exceção viraria regra."""
    from app.services.whatsapp_grupo_service import garantir_atribuicao

    user, g = _grupo(db, nome="🛍️💖🔥")
    garantir_atribuicao(db, g)
    db.flush()
    assert g.sub_id.startswith("grupo")
    assert len(g.sub_id) > len("grupo") + 4     # tem o base36 do id no meio


def test_dois_grupos_com_o_mesmo_nome_recebem_sub_ids_diferentes(db):
    """O Sub ID deixou de ser bijetivo com o id — e o índice é UNIQUE GLOBAL."""
    from app.services.whatsapp_grupo_service import garantir_atribuicao

    _u1, g1 = _grupo(db, nome="Ofertas")
    _u2, g2 = _grupo(db, nome="Ofertas")
    garantir_atribuicao(db, g1)
    garantir_atribuicao(db, g2)
    db.flush()
    assert g1.sub_id != g2.sub_id


# --- atividade --------------------------------------------------------------


def test_atividade_pagina_por_keyset_sem_repetir_linha(db):
    """
    `criado_em` empata em LOTE — uma entrada de 30 pessoas grava 30 eventos no
    mesmo instante. OFFSET sobre ordem ambígua repete e pula linhas, e o
    defeito só apareceria em campanha que está funcionando.
    """
    from app.models.campanha_link import EVENTO_ENTRADA, GrupoEvento
    from app.repositories.campanha_link_repository import CampanhaLinkRepository

    user, g = _grupo(db)
    agora = None
    for i in range(10):
        db.add(GrupoEvento(grupo_id=g.id, tipo=EVENTO_ENTRADA, origem="link",
                           identificador_hash=f"h{i}"))
    db.commit()
    # Todos com o MESMO criado_em, que é o caso que quebra o OFFSET.
    db.execute(text("UPDATE grupo_eventos SET criado_em = now() WHERE grupo_id = :g"),
               {"g": g.id})
    db.commit()

    repo = CampanhaLinkRepository(db)
    p1 = repo.atividade([g.id], limite=4)
    assert len(p1) == 5                      # limite + 1, para saber que há mais
    pagina1 = p1[:4]
    cursor = (pagina1[-1].criado_em, pagina1[-1].id)

    p2 = repo.atividade([g.id], limite=4, cursor=cursor)
    pagina2 = p2[:4]

    ids1 = {e.id for e in pagina1}
    ids2 = {e.id for e in pagina2}
    assert not (ids1 & ids2), "keyset repetiu linha entre páginas"
    assert len(ids1 | ids2) == 8


def test_atividade_filtra_por_tipo(db):
    from app.models.campanha_link import EVENTO_ENTRADA, EVENTO_SAIDA, GrupoEvento
    from app.repositories.campanha_link_repository import CampanhaLinkRepository

    user, g = _grupo(db)
    db.add(GrupoEvento(grupo_id=g.id, tipo=EVENTO_ENTRADA, origem="link",
                       identificador_hash="a"))
    db.add(GrupoEvento(grupo_id=g.id, tipo=EVENTO_SAIDA, origem="desconhecida",
                       identificador_hash="b"))
    db.commit()

    repo = CampanhaLinkRepository(db)
    assert all(e.tipo == EVENTO_SAIDA
               for e in repo.atividade([g.id], tipo=EVENTO_SAIDA))


# --- "não há medição" x "não vendeu" ----------------------------------------


def test_sub_id_de_grupo_sem_pedido_NAO_conta_como_medicao(db):
    """
    O sub_id do grupo nasce na ATIVAÇÃO, sempre — e só captura venda se as
    ofertas do grupo usarem os links do MarketDash. Contar a mera existência
    dele faria "Lucro −R$1.305,73" continuar aparecendo como prejuízo medido
    onde ninguém mediu nada.
    """
    from datetime import date as _date

    from app.models.campanha_grupos import Campanha, CampanhaGrupo
    from app.services.campanha_resultado_service import CampanhaResultadoService

    user, g = _grupo(db)
    g.sub_id = f"grupo{uuid.uuid4().hex[:8]}"      # tem sub_id, nunca vendeu
    c = Campanha(user_id=user.id, nome="Sem venda")
    db.add(c); db.flush()
    db.add(CampanhaGrupo(campanha_id=c.id, grupo_id=g.id, posicao=0))
    db.commit()

    hoje = _date.today()
    r = CampanhaResultadoService(db).por_grupo(user.id, c, hoje, hoje)
    assert r["totais"]["sub_ids_vinculados"] == 0, (
        "sub_id de grupo sem pedido não pode contar como medição"
    )


def test_sub_id_vinculado_a_mao_conta_como_medicao_mesmo_sem_venda(db):
    """Vínculo manual é declaração: ela disse o que rastrear, então zero
    comissão ali é informação — não ausência de medição."""
    from datetime import date as _date

    from app.models.campanha_grupos import Campanha, CampanhaGrupo, CampanhaSubId
    from app.services.campanha_resultado_service import CampanhaResultadoService

    user, g = _grupo(db)
    c = Campanha(user_id=user.id, nome="Com vínculo manual")
    db.add(c); db.flush()
    db.add(CampanhaGrupo(campanha_id=c.id, grupo_id=g.id, posicao=0))
    db.add(CampanhaSubId(campanha_id=c.id, sub_id="promo1"))
    db.commit()

    hoje = _date.today()
    r = CampanhaResultadoService(db).por_grupo(user.id, c, hoje, hoje)
    assert r["totais"]["sub_ids_vinculados"] == 1
