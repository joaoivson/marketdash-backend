"""Regras do CRUD: validação para publicar, normalização e duplicação."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.encryption import encrypt_value
from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    AUTOMACAO_RASCUNHO,
    CONEXAO_ATIVA,
    ESCOPO_QUALQUER,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import InstagramAutomationCreate
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
            # Sem isto o gate de ativação recusa: automação ativa com a conta fora
            # do webhook mentiria pra aluna.
            webhook_subscrito=True,
        )
    )
    s.commit()
    yield s
    s.close()


@pytest.fixture
def svc(db):
    return InstagramAutomationService(InstagramAutomationRepository(db))


def _payload(**kwargs) -> InstagramAutomationCreate:
    base = dict(
        nome="Comente QUERO",
        escopo="post_especifico",
        media_id="1800",
        trigger_tipo="palavras",
        palavras=["QUERO", "Eu Quero"],
        resposta_publica_ativa=True,
        resposta_publica_variacoes=["Te mandei!", "Já foi", "Corre lá"],
        dm_texto="link https://x",
        status=AUTOMACAO_ATIVA,
    )
    base.update(kwargs)
    return InstagramAutomationCreate(**base)


class TestNormalizacaoNaGravacao:
    @pytest.mark.asyncio
    async def test_palavras_ficam_normalizadas_e_o_original_e_preservado(self, svc, db):
        criada = await svc.criar(1, _payload(palavras=["QUERO", "Quéro!!"]))
        # A tela mostra o que a aluna digitou...
        assert criada.palavras == ["QUERO", "Quéro!!"]
        # ...e o matching usa a versão normalizada, sem repetição.
        linha = db.query(InstagramAutomation).one()
        assert linha.palavras == ["quero"]

    @pytest.mark.asyncio
    async def test_escopo_qualquer_nao_guarda_media_id(self, svc, db):
        """Guardar o post aqui faria `cobre_media` responder pelo post errado."""
        await svc.criar(1, _payload(escopo=ESCOPO_QUALQUER, media_id="1800"))
        assert db.query(InstagramAutomation).one().media_id is None


class TestValidacaoParaPublicar:
    @pytest.mark.asyncio
    async def test_rascunho_pode_ficar_incompleto(self, svc):
        criada = await svc.criar(1, _payload(status=AUTOMACAO_RASCUNHO, palavras=[], dm_texto=""))
        assert criada.status == AUTOMACAO_RASCUNHO

    @pytest.mark.asyncio
    async def test_ativa_sem_palavra_chave_e_recusada(self, svc):
        with pytest.raises(HTTPException) as exc:
            await svc.criar(1, _payload(palavras=[]))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_ativa_sem_texto_de_dm_e_recusada(self, svc):
        with pytest.raises(HTTPException) as exc:
            await svc.criar(1, _payload(dm_texto="   "))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_ativa_sem_post_escolhido_e_recusada(self, svc):
        with pytest.raises(HTTPException) as exc:
            await svc.criar(1, _payload(media_id=None))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_resposta_publica_ligada_e_vazia_e_recusada(self, svc):
        with pytest.raises(HTTPException) as exc:
            await svc.criar(1, _payload(resposta_publica_variacoes=[]))
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_mais_de_cinco_variacoes_e_recusado_no_schema(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            _payload(resposta_publica_variacoes=["a", "b", "c", "d", "e", "f"])


class TestDuplicar:
    @pytest.mark.asyncio
    async def test_copia_nasce_pausada(self, svc):
        """Duas automações idênticas ativas no mesmo post brigariam pelo comentário."""
        original = await svc.criar(1, _payload())
        copia = svc.duplicar(1, original.id)
        assert copia.status == "pausada"
        assert copia.nome.endswith("(cópia)")
        assert copia.palavras == original.palavras


class TestToggle:
    @pytest.mark.asyncio
    async def test_ativar_automacao_incompleta_e_recusado(self, svc):
        rascunho = await svc.criar(1, _payload(status=AUTOMACAO_RASCUNHO, dm_texto=""))
        with pytest.raises(HTTPException) as exc:
            await svc.alterar_status(1, rascunho.id, AUTOMACAO_ATIVA)
        assert exc.value.status_code == 422
        assert "mensagem do direct" in str(exc.value.detail)

    @pytest.mark.asyncio
    async def test_pausar_sempre_funciona(self, svc):
        ativa = await svc.criar(1, _payload())
        assert (await svc.alterar_status(1, ativa.id, "pausada")).status == "pausada"


class TestGateDeWebhook:
    """Automação não pode ficar ativa se a conta não está recebendo comentários."""

    @pytest.mark.asyncio
    async def test_ativar_com_webhook_fora_do_ar_e_recusado(self, svc, db, monkeypatch):
        from app.services import instagram_login_client as ig

        ativa = await svc.criar(1, _payload())

        # A conta perde a inscrição (ex.: perfil virou privado).
        conexao = db.query(InstagramConnection).one()
        conexao.webhook_subscrito = False
        conexao.webhook_erro = "perfil privado"
        db.commit()

        # E a tentativa de reparo automático também falha.
        async def _subscribe(_t, _i):
            raise ig.InstagramApiError("conta privada", codigo=100, permanente=True)

        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        with pytest.raises(HTTPException) as exc:
            await svc.alterar_status(1, ativa.id, AUTOMACAO_ATIVA)

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "WEBHOOK_NAO_ATIVO"

    @pytest.mark.asyncio
    async def test_ativar_repara_a_inscricao_em_vez_de_so_recusar(self, svc, db, monkeypatch):
        """Auto-reparo: se só faltava inscrever, a ativação resolve na hora."""
        from app.services import instagram_login_client as ig

        automacao = await svc.criar(1, _payload(status=AUTOMACAO_RASCUNHO))
        conexao = db.query(InstagramConnection).one()
        conexao.webhook_subscrito = False
        db.commit()

        async def _subscribe(_t, _i):
            return True

        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        resultado = await svc.alterar_status(1, automacao.id, AUTOMACAO_ATIVA)

        assert resultado.status == AUTOMACAO_ATIVA
        db.refresh(conexao)
        assert conexao.webhook_subscrito is True

    @pytest.mark.asyncio
    async def test_pausar_nao_exige_webhook(self, svc, db):
        """Desligar sempre pode — travar isso prenderia a aluna numa automação ruim."""
        ativa = await svc.criar(1, _payload())
        conexao = db.query(InstagramConnection).one()
        conexao.webhook_subscrito = False
        db.commit()

        assert (await svc.alterar_status(1, ativa.id, "pausada")).status == "pausada"
