"""Anúncios na tela de seleção (migration 085, 17/09/2026).

O que estes testes protegem:
- só aparece como anúncio o que tem PROVA: ad_id no webhook ou a Graph dizendo
  media_product_type = "AD" (a regra antiga, "fora das orgânicas", marcou o id
  falso do simulador como anúncio);
- post do feed lido com sucesso sai da lista e não é relido;
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
from app.services import instagram_anuncios_service as anuncios
from app.services import instagram_login_client as ig

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
async def test_so_aparece_como_anuncio_o_que_a_graph_diz_que_e_ad(db, conexao, monkeypatch):
    _midia(db, conexao, "reel-do-feed")
    _midia(db, conexao, "anuncio")
    _graph(monkeypatch, {
        "reel-do-feed": {"id": "reel-do-feed", "media_product_type": "REELS"},
        "anuncio": {"id": "anuncio", "media_product_type": "AD"},
    })

    pagina = await _servico(db).listar(1)

    assert [i.id for i in pagina.items] == ["anuncio"]
    assert pagina.items[0].eh_anuncio is True
    vereditos = {m.media_id: m.eh_anuncio for m in db.query(InstagramMidiaDetectada).all()}
    assert vereditos == {"reel-do-feed": False, "anuncio": True}


@pytest.mark.asyncio
async def test_id_que_a_graph_nao_le_nao_vira_anuncio(db, conexao, monkeypatch):
    """O caso real: o id falso do simulador (999…) não está no feed e a Graph dá erro."""
    _midia(db, conexao, "999999999999999")
    _graph(monkeypatch, {"999999999999999": ig.InstagramApiError("unexpected", codigo=2, permanente=False)})

    pagina = await _servico(db).listar(1)

    assert pagina.items == []
    assert db.query(InstagramMidiaDetectada).one().eh_anuncio is None


@pytest.mark.asyncio
async def test_veredito_antigo_sem_prova_e_desfeito(db, conexao, monkeypatch):
    """Linha marcada pela regra das orgânicas, com leitura que falhou: sai da tela."""
    _midia(db, conexao, "999", eh_anuncio=True, metadados_erro="x",
           metadados_lidos_em=AGORA - timedelta(hours=1))
    _graph(monkeypatch, {})

    pagina = await _servico(db).listar(1)

    assert pagina.items == []
    assert db.query(InstagramMidiaDetectada).one().eh_anuncio is None


@pytest.mark.asyncio
async def test_post_do_feed_nao_e_relido_na_abertura_seguinte(db, conexao, monkeypatch):
    _midia(db, conexao, "reel")
    lidas = _graph(monkeypatch, {"reel": {"id": "reel", "media_product_type": "REELS"}})

    await _servico(db).listar(1)
    await _servico(db).listar(1)

    assert lidas == ["reel"]


@pytest.mark.asyncio
async def test_ad_id_do_webhook_basta_mesmo_se_a_graph_falhar(db, conexao, monkeypatch):
    _midia(db, conexao, "com-ad-id", ad_id="120", ad_title="Calcinhas", eh_anuncio=True)
    _graph(monkeypatch, {"com-ad-id": ig.InstagramApiError("fora", codigo=2, permanente=False)})

    pagina = await _servico(db).listar(1)

    assert [i.id for i in pagina.items] == ["com-ad-id"]
    assert pagina.items[0].ad_title == "Calcinhas"


@pytest.mark.asyncio
async def test_metadados_da_graph_viram_miniatura_legenda_e_palavra(db, conexao, monkeypatch):
    _midia(db, conexao, "anuncio")
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
    _midia(db, conexao, "anuncio", eh_anuncio=True, ad_id="120", ad_title="Oferta")
    automacao = InstagramAutomation(
        user_id=1, connection_id=conexao.id, nome="x", escopo=ESCOPO_POST_ESPECIFICO,
        media_id="outro", trigger_tipo=TRIGGER_PALAVRAS, palavras=["quero"],
        palavras_exibicao=["quero"], resposta_publica_variacoes=[], dm_texto="t",
        status=AUTOMACAO_ATIVA,
    )
    db.add(automacao)
    db.commit()
    _graph(monkeypatch, {"anuncio": ig.InstagramApiError("token", codigo=190, permanente=True)})

    pagina = await _servico(db).listar(1)

    assert pagina.items[0].caption_preview == "Oferta"
    db.refresh(automacao)
    assert automacao.status == AUTOMACAO_ATIVA, "abrir a tela não pode pausar automação"
    midia = db.query(InstagramMidiaDetectada).one()
    assert midia.metadados_erro and midia.metadados_lidos_em is not None


@pytest.mark.asyncio
async def test_falha_recente_nao_relê_a_cada_abertura(db, conexao, monkeypatch):
    _midia(db, conexao, "anuncio", eh_anuncio=True, ad_id="120", metadados_erro="x",
           metadados_lidos_em=AGORA - timedelta(hours=1))
    lidas = _graph(monkeypatch, {})
    await _servico(db).listar(1)
    assert lidas == []


@pytest.mark.asyncio
async def test_anuncio_com_automacao_ativa_vem_marcado_e_ordem_e_do_mais_recente(db, conexao, monkeypatch):
    _midia(db, conexao, "antigo", horas_atras=30, eh_anuncio=True, media_product_type="AD",
           metadados_lidos_em=AGORA)
    _midia(db, conexao, "recente", horas_atras=1, eh_anuncio=True, media_product_type="AD",
           metadados_lidos_em=AGORA)
    db.add(InstagramAutomation(
        user_id=1, connection_id=conexao.id, nome="x", escopo=ESCOPO_POST_ESPECIFICO,
        media_id="antigo", trigger_tipo=TRIGGER_PALAVRAS, palavras=["quero"],
        palavras_exibicao=["quero"], resposta_publica_variacoes=[], dm_texto="t",
        status=AUTOMACAO_ATIVA,
    ))
    db.commit()
    _graph(monkeypatch, {})

    itens = (await _servico(db).listar(1)).items

    assert [i.id for i in itens] == ["recente", "antigo"]
    assert [i.tem_automacao for i in itens] == [False, True]


@pytest.mark.asyncio
async def test_sem_conexao_ativa_recusa(db, monkeypatch):
    with pytest.raises(HTTPException) as exc:
        await _servico(db).listar(1)
    assert exc.value.status_code == 409


# --------------------------------------------------------------------------- #
#  Vínculo anúncio → automação do produto (migration 086)                      #
# --------------------------------------------------------------------------- #


def _automacao_post(db, conexao, media_id="post-1", **kw):
    a = InstagramAutomation(
        user_id=kw.pop("user_id", 1), connection_id=conexao.id, nome="Produto",
        escopo=kw.pop("escopo", ESCOPO_POST_ESPECIFICO), media_id=media_id,
        trigger_tipo=TRIGGER_PALAVRAS, palavras=["quero"], palavras_exibicao=["quero"],
        resposta_publica_variacoes=[], dm_texto="t", status=kw.pop("status", AUTOMACAO_ATIVA),
    )
    db.add(a)
    db.commit()
    return a


@pytest.mark.asyncio
async def test_vincular_define_a_lista_completa(db, conexao, monkeypatch):
    automacao = _automacao_post(db, conexao)
    _midia(db, conexao, "ad-1", eh_anuncio=True, automation_id=automacao.id)
    _midia(db, conexao, "ad-2", eh_anuncio=True)
    _midia(db, conexao, "ad-3", eh_anuncio=True)

    resp = await _servico(db).vincular(1, automacao.id, ["ad-2", "ad-3"])

    assert sorted(resp.anuncios_vinculados) == ["ad-2", "ad-3"]
    vinculos = {m.media_id: m.automation_id for m in db.query(InstagramMidiaDetectada).all()}
    assert vinculos == {"ad-1": None, "ad-2": automacao.id, "ad-3": automacao.id}


@pytest.mark.asyncio
async def test_anuncio_pertence_a_uma_automacao_so(db, conexao, monkeypatch):
    primeira = _automacao_post(db, conexao, media_id="post-1")
    segunda = _automacao_post(db, conexao, media_id="post-2")
    _midia(db, conexao, "ad-1", eh_anuncio=True, automation_id=primeira.id)

    await _servico(db).vincular(1, segunda.id, ["ad-1"])

    assert db.query(InstagramMidiaDetectada).one().automation_id == segunda.id


@pytest.mark.asyncio
async def test_vincular_recusa_midia_que_nao_e_anuncio_da_conta(db, conexao, monkeypatch):
    automacao = _automacao_post(db, conexao)
    _midia(db, conexao, "post-do-feed", eh_anuncio=False)

    with pytest.raises(HTTPException) as exc:
        await _servico(db).vincular(1, automacao.id, ["post-do-feed", "inventado"])

    assert exc.value.status_code == 422
    assert db.query(InstagramMidiaDetectada).one().automation_id is None


@pytest.mark.asyncio
async def test_vincular_em_automacao_de_outra_aluna_da_404(db, conexao, monkeypatch):
    automacao = _automacao_post(db, conexao)
    with pytest.raises(HTTPException) as exc:
        await _servico(db).vincular(2, automacao.id, [])
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_listagem_marca_anuncio_vinculado_a_automacao_ativa(db, conexao, monkeypatch):
    automacao = _automacao_post(db, conexao)
    _midia(db, conexao, "ad-1", eh_anuncio=True, media_product_type="AD",
           metadados_lidos_em=AGORA, automation_id=automacao.id)
    _graph(monkeypatch, {})

    item = (await _servico(db).listar(1)).items[0]

    assert item.automation_id_vinculada == automacao.id
    assert item.tem_automacao is True


@pytest.mark.asyncio
async def test_vincular_ignora_a_midia_principal_da_propria_automacao(db, conexao, monkeypatch):
    """Automação criada NO anúncio: marcar o mesmo anúncio não pode duplicar a linha."""
    automacao = _automacao_post(db, conexao, media_id="ad-principal")
    _midia(db, conexao, "ad-principal", eh_anuncio=True)
    _midia(db, conexao, "ad-2", eh_anuncio=True)

    resp = await _servico(db).vincular(1, automacao.id, ["ad-principal", "ad-2"])

    assert resp.anuncios_vinculados == ["ad-2"]

