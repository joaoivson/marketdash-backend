"""Campanhas de grupos (F2): CRUD, limite do plano, ownership e composição."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.campanha_grupos import Campanha, CampanhaGrupo, CampanhaNumero
from app.models.custom_link import CustomLink
from app.models.whatsapp_grupos import (
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.services.campanha_grupos_service import (
    CampanhaGruposService, GrupoInvalido, LimiteDeCampanhas,
)

USUARIA = 1
OUTRA = 2


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for t in (CustomLink.__table__, WhatsappGrupo.__table__,
              WhatsappInstancia.__table__, WhatsappGrupoInstancia.__table__,
              Campanha.__table__, CampanhaGrupo.__table__,
              CampanhaNumero.__table__):
        t.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _grupo(db, user_id=USUARIA, jid=None, nome="Achadinhos"):
    g = WhatsappGrupo(user_id=user_id, jid=jid or f"12036{user_id}{nome}@g.us",
                      nome=nome, ativo=True, participantes=10,
                      sou_admin=True, permite_envio=True)
    db.add(g)
    db.commit()
    return g


def test_criar_listar_e_atualizar(db):
    svc = CampanhaGruposService(db)
    c = svc.criar(USUARIA, "  Ofertas Shopee  ", "desc")
    assert c.nome == "Ofertas Shopee"
    assert c.status == "ativa"

    svc.atualizar(c, {"status": "pausada", "estrategia_entrada": "aleatoria",
                      "modo_imagem": "imagem_normal", "prefixo": "🔥 ",
                      "abertura_automatica": False})
    assert (c.status, c.estrategia_entrada, c.modo_imagem) == (
        "pausada", "aleatoria", "imagem_normal")
    assert c.abertura_automatica is False

    # Valor fora do vocabulário é ignorado, não gravado
    svc.atualizar(c, {"status": "explodida"})
    assert c.status == "pausada"

    campanhas, contagens = svc.listar(USUARIA)
    assert [x.id for x in campanhas] == [c.id]
    assert contagens == {}


def test_limite_do_plano_conta_so_nao_arquivadas(db):
    svc = CampanhaGruposService(db, plan_limit_campanhas=1)
    c = svc.criar(USUARIA, "Primeira")
    with pytest.raises(LimiteDeCampanhas):
        svc.criar(USUARIA, "Segunda")
    # Arquivar libera a vaga — arquivada não some, mas não conta
    svc.atualizar(c, {"status": "arquivada"})
    svc.criar(USUARIA, "Segunda")


def test_plano_sem_recurso_e_plano_insuficiente(db):
    with pytest.raises(LimiteDeCampanhas) as e:
        CampanhaGruposService(db, plan_limit_campanhas=0).criar(USUARIA, "X")
    assert "PLANO_INSUFICIENTE" in str(e.value)


def test_definir_grupos_substitui_ordena_e_valida_dono(db):
    svc = CampanhaGruposService(db)
    c = svc.criar(USUARIA, "Rotativo")
    g1, g2, g3 = _grupo(db, nome="A"), _grupo(db, nome="B"), _grupo(db, nome="C")

    svc.definir_grupos(c, [(g1.id, 0, True), (g2.id, 1, True)])
    pares = svc.grupos_da_campanha(c)
    assert [(v.grupo_id, v.posicao) for v, _ in pares] == [(g1.id, 0), (g2.id, 1)]

    # Arrastar: g2 vai pra frente; g1 sai; g3 entra fechado
    svc.definir_grupos(c, [(g2.id, 0, True), (g3.id, 1, False)])
    pares = svc.grupos_da_campanha(c)
    assert [(v.grupo_id, v.posicao, v.aberto) for v, _ in pares] == [
        (g2.id, 0, True), (g3.id, 1, False)]

    # Grupo de OUTRA usuária: rejeitado inteiro, nada muda
    alheio = _grupo(db, user_id=OUTRA, nome="Z")
    with pytest.raises(GrupoInvalido):
        svc.definir_grupos(c, [(alheio.id, 0, True)])
    assert len(svc.grupos_da_campanha(c)) == 2


def test_mesmo_grupo_em_duas_campanhas(db):
    svc = CampanhaGruposService(db)
    g = _grupo(db)
    c1, c2 = svc.criar(USUARIA, "Uma"), svc.criar(USUARIA, "Outra")
    svc.definir_grupos(c1, [(g.id, 0, True)])
    svc.definir_grupos(c2, [(g.id, 0, True)])
    _, contagens = svc.listar(USUARIA)
    assert contagens == {c1.id: 1, c2.id: 1}


def test_obter_nao_vaza_campanha_alheia(db):
    svc = CampanhaGruposService(db)
    c = svc.criar(USUARIA, "Minha")
    assert svc.obter(OUTRA, c.id) is None


def test_grupo_duplicado_no_payload_nao_estoura_a_pk(db):
    # A tela manda a lista completa; payload com repetição não pode virar 500
    # no meio do "Salvar ordem" — vale a ÚLTIMA ocorrência.
    svc = CampanhaGruposService(db)
    c = svc.criar(USUARIA, "Dup")
    g = _grupo(db)
    svc.definir_grupos(c, [(g.id, 0, True), (g.id, 3, False)])
    pares = svc.grupos_da_campanha(c)
    assert [(v.grupo_id, v.posicao, v.aberto) for v, _ in pares] == [(g.id, 3, False)]


def test_nome_so_de_espacos_e_recusado(db):
    with pytest.raises(ValueError):
        CampanhaGruposService(db).criar(USUARIA, "   ")


def test_desarquivar_reconta_o_limite_do_plano(db):
    svc = CampanhaGruposService(db, plan_limit_campanhas=1)
    a = svc.criar(USUARIA, "A")
    svc.atualizar(a, {"status": "arquivada"})
    svc.criar(USUARIA, "B")
    # Desarquivar A estouraria o limite de 1 ativa — mesmo invariante do criar.
    with pytest.raises(LimiteDeCampanhas):
        svc.atualizar(a, {"status": "ativa"})
    assert a.status == "arquivada"
