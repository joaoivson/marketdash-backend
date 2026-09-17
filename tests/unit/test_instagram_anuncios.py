"""Anúncios na tela de seleção (migration 085, 17/09/2026).

O que estes testes protegem:
- post do feed NUNCA aparece como anúncio (confere contra as orgânicas);
- sem a lista orgânica inteira, não se afirma nada (fica para a próxima abertura);
- ad_id no webhook dispensa a conferência;
- metadados vêm da Graph, e falha deles não some com o anúncio nem pausa nada.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    ESCOPO_POST_ESPECIFICO,
    TRIGGER_PALAVRAS,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
    InstagramMidiaDetectada,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import InstagramMediaItem, InstagramMediaPage
from app.services import instagram_anuncios_service as anuncios
from app.services import instagram_login_client as ig
from app.services.instagram_automation_service import InstagramAutomationService

AGORA = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            InstagramConnection.__table__,
            InstagramAutomation.__table__,
            InstagramEvent.__table__,
            InstagramMidiaDetectada.__table__,
        ],
    )
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


@pytest.fixture
def conexao(db):
    from app.core.encryption import encrypt_value

    c = InstagramConnection(
        user_id=1,
        ig_user_id="17841400000000000",
        ig_username="aluna",
        access_token=encrypt_value("token"),
        token_expires_at=AGORA + timedelta(days=50),
        status=CONEXAO_ATIVA,
    )
    db.add(c)
    db.commit()
    return c


def _midia(db, conexao, media_id, horas_atras=1, **kw):
    m = InstagramMidiaDetectada(
        user_id=1,
        connection_id=conexao.id,
        media_id=media_id,
        comentarios=kw.pop("comentarios", 3),
        ultimo_comentario_em=AGORA - timedelta(hours=horas_atras),
        **kw,
    )
    db.add(m)
    db.commit()
    return m


def _organicas(monkeypatch, paginas, falha=False):
    chamadas = []

    async def _listar(self, user_id, cursor=None, forcar=False):
        chamadas.append(cursor)
        if falha:
            raise HTTPException(status_code=400, detail="Meta fora")
        indice = int(cursor) if cursor else 0
        return InstagramMediaPage(
            items=[InstagramMediaItem(id=i) for i in paginas[indice]],
            next_cursor=str(indice + 1) if indice + 1 < len(paginas) else None,
        )

    monkeypatch.setattr(InstagramAutomationService, "listar_midias", _listar)
    return chamadas


def _graph(monkeypatch, respostas):
    lidas = []

    async def _get(token, media_id):
        lidas.append(media_id)
        r = respostas.get(media_id)
        if isinstance(r, Exception):
            raise r
        return r or {"id": media_id}

    monkeypatch.setattr(ig, "get_media", _get)
    return lidas


def _servico(db):
    return anuncios.InstagramAnunciosService(InstagramAutomationRepository(db))


@pytest.mark.asyncio
async def test_post_do_feed_nunca_aparece_como_anuncio(db, conexao, monkeypatch):
    _midia(db, conexao, "organico-sem-automacao")
    _midia(db, conexao, "anuncio-dark-post")
    _organicas(monkeypatch, [["organico-sem-automacao", "outro"], ["mais-um"]])
    _graph(monkeypatch, {})

    pagina = await _servico(db).listar(1)

    assert [i.id for i in pagina.items] == ["anuncio-dark-post"]
    assert pagina.items[0].eh_anuncio is True
    resolvidas = {m.media_id: m.eh_anuncio for m in db.query(InstagramMidiaDetectada).all()}
    assert resolvidas == {"organico-sem-automacao": False, "anuncio-dark-post": True}


@pytest.mark.asyncio
async def test_conferencia_so_roda_uma_vez_por_midia(db, conexao, monkeypatch):
    _midia(db, conexao, "anuncio")
    chamadas = _organicas(monkeypatch, [["x"]])
    _graph(monkeypatch, {})

    await _servico(db).listar(1)
    await _servico(db).listar(1)

    assert chamadas == [None], "já resolvida, não relê as orgânicas"


@pytest.mark.asyncio
async def test_sem_a_lista_organica_inteira_nao_afirma_nada(db, conexao, monkeypatch):
    _midia(db, conexao, "desconhecida")
    _midia(db, conexao, "com-ad-id", ad_id="120", eh_anuncio=True)
    _organicas(monkeypatch, [], falha=True)
    _graph(monkeypatch, {})

    pagina = await _servico(db).listar(1)

    assert [i.id for i in pagina.items] == ["com-ad-id"]
    assert db.query(InstagramMidiaDetectada).filter_by(media_id="desconhecida").one().eh_anuncio is None


@pytest.mark.asyncio
async def test_ad_id_dispensa_a_conferencia(db, conexao, monkeypatch):
    _midia(db, conexao, "com-ad-id", ad_id="120", ad_title="Calcinhas", eh_anuncio=True)
    chamadas = _organicas(monkeypatch, [["x"]])
    _graph(monkeypatch, {})

    pagina = await _servico(db).listar(1)

    assert chamadas == []
    assert pagina.items[0].ad_title == "Calcinhas"


@pytest.mark.asyncio
async def test_metadados_da_graph_viram_miniatura_legenda_e_palavra(db, conexao, monkeypatch):
    _midia(db, conexao, "anuncio", eh_anuncio=True)
    _organicas(monkeypatch, [[]])
    _graph(monkeypatch, {"anuncio": {
        "id": "anuncio",
        "caption": 'Comente " ALGODÃO " para receber o link!',
        "thumbnail_url": "https://cdn/x.jpg",
        "permalink": "https://www.instagram.com/p/AD/",
        "media_product_type": "AD",
        "timestamp": "2026-09-12T10:00:00+0000",
    }})

    item = (await _servico(db).listar(1)).items[0]

    assert item.thumbnail_url == "https://cdn/x.jpg"
    assert item.media_product_type == "AD"
    assert item.palavra_sugerida is not None
    assert "ALGODÃO" in item.caption_preview


@pytest.mark.asyncio
async def test_falha_nos_metadados_mantem_o_anuncio_e_nao_pausa_nada(db, conexao, monkeypatch):
    _midia(db, conexao, "anuncio", eh_anuncio=True, ad_title="Oferta")
    automacao = InstagramAutomation(
        user_id=1, connection_id=conexao.id, nome="x", escopo=ESCOPO_POST_ESPECIFICO,
        media_id="outro", trigger_tipo=TRIGGER_PALAVRAS, palavras=["quero"],
        palavras_exibicao=["quero"], resposta_publica_variacoes=[], dm_texto="t",
        status=AUTOMACAO_ATIVA,
    )
    db.add(automacao)
    db.commit()
    _organicas(monkeypatch, [[]])
    _graph(monkeypatch, {"anuncio": ig.InstagramApiError("token", codigo=190, permanente=True)})

    pagina = await _servico(db).listar(1)

    assert pagina.items[0].caption_preview == "Oferta"
    db.refresh(automacao)
    assert automacao.status == AUTOMACAO_ATIVA, "abrir a tela não pode pausar automação"
    midia = db.query(InstagramMidiaDetectada).one()
    assert midia.metadados_erro and midia.metadados_lidos_em is not None


@pytest.mark.asyncio
async def test_falha_recente_nao_relê_a_cada_abertura(db, conexao, monkeypatch):
    _midia(db, conexao, "anuncio", eh_anuncio=True, metadados_erro="x",
           metadados_lidos_em=AGORA - timedelta(hours=1))
    _organicas(monkeypatch, [[]])
    lidas = _graph(monkeypatch, {})
    await _servico(db).listar(1)
    assert lidas == []


@pytest.mark.asyncio
async def test_anuncio_com_automacao_ativa_vem_marcado_e_ordem_e_do_mais_recente(db, conexao, monkeypatch):
    _midia(db, conexao, "antigo", horas_atras=30, eh_anuncio=True)
    _midia(db, conexao, "recente", horas_atras=1, eh_anuncio=True)
    db.add(InstagramAutomation(
        user_id=1, connection_id=conexao.id, nome="x", escopo=ESCOPO_POST_ESPECIFICO,
        media_id="antigo", trigger_tipo=TRIGGER_PALAVRAS, palavras=["quero"],
        palavras_exibicao=["quero"], resposta_publica_variacoes=[], dm_texto="t",
        status=AUTOMACAO_ATIVA,
    ))
    db.commit()
    _organicas(monkeypatch, [[]])
    _graph(monkeypatch, {})

    itens = (await _servico(db).listar(1)).items

    assert [i.id for i in itens] == ["recente", "antigo"]
    assert [i.tem_automacao for i in itens] == [False, True]


@pytest.mark.asyncio
async def test_sem_conexao_ativa_recusa(db, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await _servico(db).listar(1)
    assert exc.value.status_code == 409
