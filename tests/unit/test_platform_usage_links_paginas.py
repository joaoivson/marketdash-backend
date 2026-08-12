"""Rodada 6, itens 5 e 9: barras de acessos por dia e colunas Links/Páginas.

Links/Páginas medem operação construída na plataforma: quem tem link rodando em
anúncio não cancela sem dor. LEITURA APENAS — nenhuma escrita nas tabelas do
produto.

Banco SQLite em memória, mesmo padrão de tests/unit/test_platform_usage_base_ativa.py
— o comportamento das queries precisa ser exercido de verdade.
"""
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.models.capture_site import CaptureSite
from app.models.custom_link import CustomLink
from app.models.custom_link_event import CustomLinkEvent
from app.models.page_event import PageEvent
from app.models.user import User
from app.models.user_login import UserLogin
from app.services.platform_usage_service import PlatformUsageService

AGORA = datetime.now(timezone.utc)


@pytest.fixture
def db():
    """UserLogin.id é BigInteger — SQLite só autoincrementa INTEGER puro como PK
    (mesmo gotcha documentado em test_platform_usage_base_ativa.py para
    SubscriptionEvent), daí o listener atribuindo id manualmente."""
    engine = create_engine("sqlite://")
    for modelo in (User, UserLogin, CustomLink, CustomLinkEvent, CaptureSite, PageEvent):
        modelo.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()

    contador = [0]

    @event.listens_for(UserLogin, "before_insert")
    def _atribuir_id(mapper, connection, target):
        if target.id is None:
            contador[0] += 1
            target.id = contador[0]

    yield sessao
    sessao.close()
    event.remove(UserLogin, "before_insert", _atribuir_id)


def _user(db, email):
    u = User(email=email, name=email, hashed_password="x", is_admin=False, is_demo=False)
    db.add(u)
    db.flush()
    return u


def _login(db, user_id, quando):
    db.add(UserLogin(user_id=user_id, logged_at=quando))
    db.flush()


def _link(db, user_id, slug, ativo=True):
    link = CustomLink(
        user_id=user_id,
        name=slug,
        original_url="https://exemplo.com",
        slug=slug,
        is_active=ativo,
    )
    db.add(link)
    db.flush()
    return link


def _clique(db, link, quando):
    db.add(CustomLinkEvent(custom_link_id=link.id, user_id=link.user_id, created_at=quando))
    db.flush()


def _pagina(db, user_id, slug, ativa=True):
    site = CaptureSite(user_id=user_id, slug=slug, is_active=ativa)
    db.add(site)
    db.flush()
    return site


def _visualizacao(db, site, quando, tipo="page_view"):
    db.add(PageEvent(site_id=site.id, event_type=tipo, created_at=quando))
    db.flush()


def test_usuarias_por_dia_conta_hits_e_pessoas_distintas(db):
    """A barra é `acessos` (hits) e a linha é `usuarias` (distintas) — sem as
    duas chaves o gráfico da aba Uso renderiza só a linha."""
    ana = _user(db, "ana@example.com")
    bia = _user(db, "bia@example.com")
    _login(db, ana.id, AGORA - timedelta(hours=1))
    _login(db, ana.id, AGORA - timedelta(hours=2))
    _login(db, bia.id, AGORA - timedelta(hours=3))

    serie = PlatformUsageService(db).usuarias_por_dia("7d")

    assert len(serie) == 1
    assert serie[0]["acessos"] == 3
    assert serie[0]["usuarias"] == 2
    assert "date" in serie[0]


def test_link_ativo_com_clique_no_periodo_esta_em_uso(db):
    """`em uso` = ativo E com clique na janela. Só criar não conta."""
    ana = _user(db, "ana@example.com")
    com_clique = _link(db, ana.id, "rodando")
    _link(db, ana.id, "parado")
    inativo_com_clique = _link(db, ana.id, "desligado", ativo=False)
    _clique(db, com_clique, AGORA - timedelta(days=1))
    _clique(db, inativo_com_clique, AGORA - timedelta(days=1))

    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])

    assert uso[ana.id]["links_criados"] == 3
    assert uso[ana.id]["links_em_uso"] == 1


def test_clique_fora_do_periodo_nao_conta_como_em_uso(db):
    ana = _user(db, "ana@example.com")
    antigo = _link(db, ana.id, "antigo")
    _clique(db, antigo, AGORA - timedelta(days=40))

    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])

    assert uso[ana.id]["links_criados"] == 1
    assert uso[ana.id]["links_em_uso"] == 0


def test_pagina_ativa_com_visualizacao_no_periodo_esta_em_uso(db):
    ana = _user(db, "ana@example.com")
    vista = _pagina(db, ana.id, "oferta")
    _pagina(db, ana.id, "rascunho")
    _visualizacao(db, vista, AGORA - timedelta(days=2))
    # click_group não é visualização de página
    _visualizacao(db, vista, AGORA - timedelta(days=2), tipo="click_group")

    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])

    assert uso[ana.id]["paginas_criadas"] == 2
    assert uso[ana.id]["paginas_em_uso"] == 1


def test_usuaria_sem_nada_recebe_zeros(db):
    ana = _user(db, "ana@example.com")
    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])
    assert uso[ana.id] == {
        "links_em_uso": 0,
        "links_criados": 0,
        "paginas_em_uso": 0,
        "paginas_criadas": 0,
    }


def test_lista_de_usuarias_vazia_nao_consulta_nada(db):
    assert PlatformUsageService(db).uso_de_links_e_paginas("7d", []) == {}


def test_servico_nao_escreve_nas_tabelas_do_produto():
    """Guarda-corpo estático do item 9 ("leitura apenas").

    Não dá para provar ausência de escrita por comportamento — este teste
    existe para que uma escrita introduzida no futuro quebre o build.
    """
    fonte = inspect.getsource(PlatformUsageService)
    for proibido in (".add(", ".delete(", ".update(", "db.commit("):
        assert proibido not in fonte, f"platform_usage_service não pode usar {proibido}"
