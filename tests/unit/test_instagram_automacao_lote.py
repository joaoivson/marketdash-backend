"""Criação de automações em lote.

Por que o endpoint existe: `escopo = post_especifico` cobre UM post. A conta que
motivou isto (@promosdabeatrizz_, medida em 11/09/2026) tem **283 publicações
pedindo "Comente X" e 9 cobertas** — uma a uma, pela tela de edição, ela nunca
alcança. E post antigo continua recebendo comentário por meses.

O que estes testes protegem é sobretudo o que NÃO pode acontecer numa passada de
50 posts: duplicar automação no mesmo post, perder o lote inteiro por um link
mal colado, e — o pior — devolver só o total criado, escondendo o post que
continua sem responder.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.encryption import encrypt_value
from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    AUTOMACAO_RASCUNHO,
    CONEXAO_ATIVA,
    ESCOPO_POST_ESPECIFICO,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import (
    InstagramAutomacaoLoteItem,
    InstagramAutomacaoLoteRequest,
)
from app.services.instagram_automation_service import InstagramAutomationService


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(
        engine,
        tables=[
            InstagramConnection.__table__,
            InstagramAutomation.__table__,
            InstagramEvent.__table__,
        ],
    )
    s = sessionmaker(bind=engine)()
    s.add(
        InstagramConnection(
            user_id=1,
            ig_user_id="178414",
            access_token=encrypt_value("t"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=50),
            status=CONEXAO_ATIVA,
            webhook_subscrito=True,
        )
    )
    s.commit()
    yield s
    s.close()


@pytest.fixture
def svc(db):
    return InstagramAutomationService(InstagramAutomationRepository(db))


def _pedido(itens, **kwargs) -> InstagramAutomacaoLoteRequest:
    base = dict(
        itens=itens,
        dm_texto="Oi! Aqui está o link 💜",
        dm_botao_texto="Ver oferta",
        resposta_publica_ativa=True,
        resposta_publica_variacoes=["te mandei no direct 😍"],
        palavras_comuns=["quero", "eu quero"],
        status=AUTOMACAO_ATIVA,
    )
    base.update(kwargs)
    return InstagramAutomacaoLoteRequest(**base)


def _item(media_id, palavra="algodao", link="https://x.com/a", **kwargs):
    return InstagramAutomacaoLoteItem(
        media_id=media_id, palavras=[palavra], dm_link=link, **kwargs
    )


class TestCaminhoFeliz:
    @pytest.mark.asyncio
    async def test_cria_uma_automacao_por_post(self, svc):
        r = await svc.criar_em_lote(1, _pedido([_item("m1", "algodao"), _item("m2", "meia")]))

        assert len(r.criadas) == 2
        assert r.puladas == []
        assert {a.media_id for a in r.criadas} == {"m1", "m2"}
        assert all(a.escopo == ESCOPO_POST_ESPECIFICO for a in r.criadas)

    @pytest.mark.asyncio
    async def test_palavras_comuns_entram_junto_com_a_do_post(self, svc):
        """27% das pessoas comentam 'quero' em vez da palavra do produto.

        Sem as comuns, essas ficariam sem resposta mesmo com automação no post.
        """
        r = await svc.criar_em_lote(1, _pedido([_item("m1", "algodao")]))

        palavras = {p.lower() for p in r.criadas[0].palavras}
        assert "algodao" in palavras
        assert "quero" in palavras and "eu quero" in palavras

    @pytest.mark.asyncio
    async def test_o_modelo_vale_para_todos(self, svc):
        r = await svc.criar_em_lote(1, _pedido([_item("m1"), _item("m2")]))

        assert all(a.dm_texto == "Oi! Aqui está o link 💜" for a in r.criadas)
        assert all(a.dm_botao_texto == "Ver oferta" for a in r.criadas)

    @pytest.mark.asyncio
    async def test_link_e_por_post_nao_do_modelo(self, svc):
        """O link é a única coisa que NÃO pode vazar de um post para outro."""
        r = await svc.criar_em_lote(1, _pedido([
            _item("m1", link="https://loja/algodao"),
            _item("m2", link="https://loja/meia"),
        ]))

        por_media = {a.media_id: a.dm_link for a in r.criadas}
        assert por_media["m1"] == "https://loja/algodao"
        assert por_media["m2"] == "https://loja/meia"

    @pytest.mark.asyncio
    async def test_nome_sai_da_palavra_quando_nao_informado(self, svc):
        r = await svc.criar_em_lote(1, _pedido([_item("m1", "algodao")]))

        assert r.criadas[0].nome == "algodao"


class TestNaoDuplicar:
    @pytest.mark.asyncio
    async def test_post_ja_coberto_e_pulado_com_motivo(self, svc, db):
        db.add(InstagramAutomation(
            user_id=1, connection_id=1, nome="já existe",
            escopo=ESCOPO_POST_ESPECIFICO, media_id="m1", status=AUTOMACAO_ATIVA,
        ))
        db.commit()

        r = await svc.criar_em_lote(1, _pedido([_item("m1"), _item("m2")]))

        assert [a.media_id for a in r.criadas] == ["m2"]
        assert len(r.puladas) == 1
        assert r.puladas[0].media_id == "m1"
        assert "já tem automação" in r.puladas[0].motivo

    @pytest.mark.asyncio
    async def test_post_repetido_no_mesmo_lote_entra_uma_vez(self, svc):
        """Duas ativas no mesmo post disputariam o mesmo comentário."""
        r = await svc.criar_em_lote(1, _pedido([_item("m1"), _item("m1")]))

        assert len(r.criadas) == 1
        assert len(r.puladas) == 1
        assert "repetido" in r.puladas[0].motivo.lower()

    @pytest.mark.asyncio
    async def test_chamar_duas_vezes_nao_duplica(self, svc):
        """Clique duplo é a forma mais comum de duplicar sem querer."""
        await svc.criar_em_lote(1, _pedido([_item("m1")]))
        r = await svc.criar_em_lote(1, _pedido([_item("m1")]))

        assert r.criadas == []
        assert len(r.puladas) == 1


class TestFalhaParcial:
    @pytest.mark.asyncio
    async def test_item_sem_link_nao_derruba_o_lote(self, svc):
        """Recusar tudo faria a aluna recomeçar a passada inteira.

        O link é o campo que ela cola post a post — é o que mais fica em branco
        numa passada de 50.
        """
        r = await svc.criar_em_lote(1, _pedido([
            _item("m1", "algodao"),
            InstagramAutomacaoLoteItem(media_id="m2", palavras=["gel"], dm_link=None),
            _item("m3", "meia"),
        ]))

        assert {a.media_id for a in r.criadas} == {"m1", "m3"}
        assert [p.media_id for p in r.puladas] == ["m2"]
        assert r.puladas[0].motivo  # o motivo nunca vem vazio

    @pytest.mark.asyncio
    async def test_sem_palavra_nenhuma_e_pulado(self, svc):
        """Sem palavras comuns, o post cuja legenda não deu sugestão fica sem gatilho."""
        r = await svc.criar_em_lote(1, _pedido(
            [
                _item("m1", "algodao"),
                InstagramAutomacaoLoteItem(media_id="m2", palavras=[], dm_link="https://x/b"),
            ],
            palavras_comuns=[],
        ))

        assert [a.media_id for a in r.criadas] == ["m1"]
        assert [p.media_id for p in r.puladas] == ["m2"]

    @pytest.mark.asyncio
    async def test_palavras_comuns_salvam_o_post_sem_sugestao(self, svc):
        """O contraponto do teste acima: com as comuns, o post entra mesmo sem
        palavra própria — e passa a atender quem comenta 'quero'."""
        r = await svc.criar_em_lote(1, _pedido([
            InstagramAutomacaoLoteItem(media_id="m2", palavras=[], dm_link="https://x/b"),
        ]))

        assert len(r.criadas) == 1
        assert {p.lower() for p in r.criadas[0].palavras} == {"quero", "eu quero"}

    @pytest.mark.asyncio
    async def test_o_que_foi_pulado_sempre_aparece(self, svc):
        """Devolver só o total criado esconde o post que segue sem responder."""
        r = await svc.criar_em_lote(1, _pedido([
            InstagramAutomacaoLoteItem(media_id="m1", palavras=[], dm_link=None),
        ]))

        assert r.criadas == []
        assert len(r.puladas) == 1

    @pytest.mark.asyncio
    async def test_rascunho_aceita_item_incompleto(self, svc):
        """Rascunho existe justamente para salvar no meio do caminho."""
        r = await svc.criar_em_lote(1, _pedido(
            [InstagramAutomacaoLoteItem(media_id="m1", palavras=[], dm_link=None)],
            status=AUTOMACAO_RASCUNHO,
        ))

        assert len(r.criadas) == 1
        assert r.puladas == []


class TestLimites:
    def test_lote_vazio_e_recusado_no_schema(self):
        with pytest.raises(Exception):
            InstagramAutomacaoLoteRequest(itens=[], dm_texto="oi")

    def test_acima_do_teto_e_recusado_no_schema(self):
        from app.schemas.instagram_automation import MAX_ITENS_LOTE

        with pytest.raises(Exception):
            InstagramAutomacaoLoteRequest(
                itens=[_item(f"m{i}") for i in range(MAX_ITENS_LOTE + 1)], dm_texto="oi"
            )
