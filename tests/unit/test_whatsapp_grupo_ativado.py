"""
Toggle "Ativo" do grupo (spec §6.2/6.3) — WhatsappGrupoService.definir_ativado.

As decisões que não podem regredir:

  * ativar é o PONTO DE ATRIBUIÇÃO: sub_id (`wg`+base36) e custom_link nascem
    na MESMA transação do toggle — e NUNCA são regenerados;
  * o limite do plano vale só quando o count de ativados vai CRESCER —
    repetir o PATCH num grupo já ativado não pode tomar 403;
  * desativar é só a flag: nada é apagado.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.custom_link import CustomLink
from app.models.whatsapp_grupos import (
    WhatsappGrupo, WhatsappGrupoInstancia, WhatsappInstancia,
)
from app.services.whatsapp_grupo_service import (
    LimiteDeGruposAtivados, WhatsappGrupoService,
)
from app.services.whatsapp_grupo_sync_service import sub_id_do_grupo  # noqa: F401

USUARIA = 1


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for t in (CustomLink.__table__, WhatsappInstancia.__table__,
              WhatsappGrupo.__table__, WhatsappGrupoInstancia.__table__):
        t.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _grupo(db, i=1, ativado=False):
    g = WhatsappGrupo(user_id=USUARIA, jid=f"12036300000000000{i}@g.us",
                      nome=f"G{i}", ativo=True, ativado=ativado)
    db.add(g)
    db.commit()
    return g


def _servico(db, limite=-1):
    return WhatsappGrupoService(db, plan_limit_grupos=limite)


def test_ativar_cria_sub_id_e_custom_link_na_mesma_transacao(db):
    grupo = _grupo(db)
    assert grupo.sub_id is None and grupo.custom_link_id is None

    grupo = _servico(db).definir_ativado(grupo, True)

    assert grupo.ativado is True
    # Formato legível desde 05/09 (`grupobeatriz2k7f`). Asserção de PROPRIEDADE,
    # não de igualdade: o sufixo é aleatório de propósito, porque o Sub ID
    # deixou de ser bijetivo com o id e o índice é UNIQUE global.
    import re as _re
    assert _re.fullmatch(r"grupo[a-z0-9]+", grupo.sub_id), grupo.sub_id
    assert len(grupo.sub_id) <= 64
    link = db.query(CustomLink).one()
    assert grupo.custom_link_id == link.id
    assert link.tag == "whatsapp"    # fora de Meus Links pela FK, não pela tag


def test_repetir_o_patch_e_idempotente_nao_regenera_nada(db):
    grupo = _servico(db).definir_ativado(_grupo(db), True)
    sub_id, link_id = grupo.sub_id, grupo.custom_link_id

    grupo = _servico(db, limite=1).definir_ativado(grupo, True)   # repetiu

    assert grupo.sub_id == sub_id                  # NUNCA regenerar
    assert grupo.custom_link_id == link_id
    assert db.query(CustomLink).count() == 1
    # e com o limite CHEIO: religar grupo já ativado não pode tomar 403


def test_limite_do_plano_barra_o_grupo_que_excede(db):
    _servico(db).definir_ativado(_grupo(db, 1), True)
    segundo = _grupo(db, 2)

    with pytest.raises(LimiteDeGruposAtivados) as e:
        _servico(db, limite=1).definir_ativado(segundo, True)

    assert e.value.limite == 1                     # a rota monta o 403 com isso
    db.rollback()
    assert segundo.ativado is False
    assert db.query(CustomLink).count() == 1       # nada criado para o barrado


def test_ilimitado_nao_barra_nunca(db):
    for i in range(1, 4):
        _servico(db, limite=-1).definir_ativado(_grupo(db, i), True)
    assert db.query(WhatsappGrupo).filter(WhatsappGrupo.ativado.is_(True)).count() == 3


def test_desativar_e_so_a_flag_nada_e_apagado(db):
    grupo = _servico(db).definir_ativado(_grupo(db), True)
    sub_id, link_id = grupo.sub_id, grupo.custom_link_id

    grupo = _servico(db).definir_ativado(grupo, False)

    assert grupo.ativado is False
    assert grupo.sub_id == sub_id                  # atribuição preservada
    assert grupo.custom_link_id == link_id
    assert db.query(CustomLink).count() == 1


def test_reativar_mantem_a_atribuicao_original(db):
    svc = _servico(db)
    grupo = svc.definir_ativado(_grupo(db), True)
    sub_id = grupo.sub_id
    svc.definir_ativado(grupo, False)

    grupo = svc.definir_ativado(grupo, True)

    assert grupo.sub_id == sub_id                  # liga-desliga não regenera
    assert db.query(CustomLink).count() == 1       # nem cria segundo link


def test_reativar_com_o_limite_ocupado_por_outro_grupo_barra(db):
    """Desativou A, ativou B: religar A com limite 1 excede — barra."""
    svc = _servico(db, limite=1)
    a = svc.definir_ativado(_grupo(db, 1), True)
    svc.definir_ativado(a, False)
    svc.definir_ativado(_grupo(db, 2), True)

    with pytest.raises(LimiteDeGruposAtivados):
        svc.definir_ativado(a, True)
