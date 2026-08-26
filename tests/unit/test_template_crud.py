"""CRUD de templates e variações (F4)."""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.roteiro import TemplateMensagem, TemplateVariacao
from app.services.template_mensagem_service import (
    TemplateInvalido, TemplateMensagemService, sortear_variacao,
)

USUARIA, OUTRA = 1, 2


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    TemplateMensagem.__table__.create(engine)
    TemplateVariacao.__table__.create(engine)
    s = sessionmaker(bind=engine)()
    yield s
    s.close()


def test_criar_listar_e_contar_variacoes_sem_n_mais_um(db):
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "  Achadinhos  ")
    assert t.nome == "Achadinhos"
    svc.definir_variacoes(t, [("corpo A {link}", 1, True), ("corpo B {link}", 3, True)])

    templates, variacoes = svc.listar(USUARIA)
    assert [x.id for x in templates] == [t.id]
    assert len(variacoes[t.id]) == 2


def test_nome_vazio_e_recusado(db):
    with pytest.raises(TemplateInvalido):
        TemplateMensagemService(db).criar(USUARIA, "   ")


def test_definir_variacoes_substitui_e_normaliza_peso(db):
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "T")
    svc.definir_variacoes(t, [("a {link}", 5, True)])
    svc.definir_variacoes(t, [("b {link}", 0, True), ("  ", 9, True)])

    from app.repositories.template_repository import TemplateRepository
    vs = TemplateRepository(db).variacoes(t.id)
    assert [v.corpo for v in vs] == ["b {link}"]     # substituiu, ignorou vazia
    assert vs[0].peso == 1                            # peso 0 vira 1


def test_template_sem_variacao_valida_e_recusado(db):
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "T")
    with pytest.raises(TemplateInvalido):
        svc.definir_variacoes(t, [("   ", 1, True)])


def test_acrescentar_da_ia_nao_duplica_nem_substitui(db):
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "T")
    svc.definir_variacoes(t, [("original {link}", 1, True)])
    n = svc.acrescentar_variacoes(t, ["nova {link}", "original {link}", "  "])

    from app.repositories.template_repository import TemplateRepository
    corpos = [v.corpo for v in TemplateRepository(db).variacoes(t.id)]
    assert n == 1
    assert corpos == ["original {link}", "nova {link}"]


def test_remover_e_soft_delete_some_da_lista_mas_nao_do_banco(db):
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "T")
    svc.remover(t)
    templates, _ = svc.listar(USUARIA)
    assert templates == []
    assert db.query(TemplateMensagem).count() == 1   # passo de roteiro ainda aponta


def test_nao_vaza_template_de_outra_usuaria(db):
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "Meu")
    assert svc.obter(OUTRA, t.id) is None


def test_sorteio_usa_as_variacoes_salvas(db):
    import random
    svc = TemplateMensagemService(db)
    t = svc.criar(USUARIA, "T")
    svc.definir_variacoes(t, [("raro {link}", 1, True), ("comum {link}", 50, True)])

    from app.repositories.template_repository import TemplateRepository
    vs = TemplateRepository(db).variacoes(t.id)
    escolhas = [sortear_variacao(vs, random.Random(7)).corpo for _ in range(50)]
    assert escolhas.count("comum {link}") > 40
