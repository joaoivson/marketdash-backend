"""Inscrição da CONTA no webhook (`subscribed_apps`) — o silêncio mais caro da integração.

Assinar o campo `comments` no App Dashboard vale para o APP. Cada conta conectada
precisa de uma chamada própria (passo 3 dos 4 da Meta). Sem ela o webhook nunca
dispara e **não há erro em canto nenhum**: OAuth funciona, tela funciona, automação
salva, e nada acontece.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.encryption import encrypt_value
from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.services import instagram_login_client as ig
from app.services.instagram_connection_service import InstagramConnectionService

IG_USER_ID = "17841400000000000"


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
    yield s
    s.close()


@pytest.fixture
def svc(db):
    return InstagramConnectionService(InstagramAutomationRepository(db))


def _conexao(db) -> InstagramConnection:
    c = InstagramConnection(
        user_id=1,
        ig_user_id=IG_USER_ID,
        ig_username="aluna",
        access_token=encrypt_value("token"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=50),
        status=CONEXAO_ATIVA,
    )
    db.add(c)
    db.commit()
    return c


def _mockar_oauth(monkeypatch, account_type="BUSINESS"):
    async def _code(_c, _r):
        return {"access_token": "curto"}

    async def _longo(_t):
        return {"access_token": "longo", "expires_in": 5_184_000}

    async def _me(_t):
        return {
            "user_id": IG_USER_ID,
            "username": "aluna",
            "account_type": account_type,
            "profile_picture_url": None,
        }

    monkeypatch.setattr(ig, "exchange_code_for_short_token", _code)
    monkeypatch.setattr(ig, "exchange_for_long_lived_token", _longo)
    monkeypatch.setattr(ig, "get_me", _me)


class TestAssinaturaNoOAuth:
    @pytest.mark.asyncio
    async def test_conectar_inscreve_a_conta_no_webhook(self, db, svc, monkeypatch):
        chamadas = []

        async def _subscribe(token, ig_user_id):
            chamadas.append((token, ig_user_id))
            return True

        _mockar_oauth(monkeypatch)
        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        resposta = await svc.handle_oauth_callback(1, "code", "https://x/cb")

        assert len(chamadas) == 1, "a inscrição da conta precisa acontecer no OAuth"
        assert chamadas[0][1] == IG_USER_ID
        assert resposta.webhook_subscrito is True
        conexao = db.query(InstagramConnection).one()
        assert conexao.webhook_subscrito is True
        assert conexao.webhook_subscrito_em is not None
        assert conexao.webhook_erro is None

    @pytest.mark.asyncio
    async def test_falha_na_inscricao_nao_derruba_a_conexao(self, db, svc, monkeypatch):
        """Bloquear aqui tornaria impossível conectar antes do App Review.

        A doc da Meta não garante que `subscribed_apps` funcione com o app em
        Development mode. Se falhar, a conexão fica salva e o problema fica
        visível — em vez de a aluna não conseguir conectar de jeito nenhum.
        """

        async def _subscribe(_t, _i):
            raise ig.InstagramApiError(
                "conta privada não recebe notificação", codigo=100, permanente=True
            )

        _mockar_oauth(monkeypatch)
        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        resposta = await svc.handle_oauth_callback(1, "code", "https://x/cb")

        assert resposta.status == CONEXAO_ATIVA, "a conexão precisa ser preservada"
        assert resposta.webhook_subscrito is False
        assert "privada" in (resposta.webhook_erro or "")
        assert db.query(InstagramConnection).count() == 1

    @pytest.mark.asyncio
    async def test_o_id_usado_e_o_user_id_do_perfil(self, db, svc, monkeypatch):
        """`user_id` (não `id`) é o valor que chega como `entry.id` no webhook.

        Inscrever com o id errado faz a Meta aceitar a chamada e o webhook nunca
        casar com nenhuma conexão nossa.
        """
        recebidos = []

        async def _subscribe(_t, ig_user_id):
            recebidos.append(ig_user_id)
            return True

        async def _me(_t):
            # A API devolve os dois: `id` é app-scoped, `user_id` é o da conta.
            return {
                "id": "app-scoped-999",
                "user_id": IG_USER_ID,
                "username": "aluna",
                "account_type": "BUSINESS",
            }

        _mockar_oauth(monkeypatch)
        monkeypatch.setattr(ig, "get_me", _me)
        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        await svc.handle_oauth_callback(1, "code", "https://x/cb")

        assert recebidos == [IG_USER_ID]
        assert db.query(InstagramConnection).one().ig_user_id == IG_USER_ID


class TestReassinatura:
    @pytest.mark.asyncio
    async def test_renovar_token_reinscreve_a_conta(self, db, svc, monkeypatch):
        conexao = _conexao(db)
        chamadas = []

        async def _refresh(_t):
            return {"access_token": "novo", "expires_in": 5_184_000}

        async def _subscribe(_t, ig_user_id):
            chamadas.append(ig_user_id)
            return True

        monkeypatch.setattr(ig, "refresh_long_lived_token", _refresh)
        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        assert await svc.refresh_connection(conexao) is True
        assert chamadas == [IG_USER_ID]

    @pytest.mark.asyncio
    async def test_reinscricao_falhando_nao_desfaz_a_renovacao(self, db, svc, monkeypatch):
        conexao = _conexao(db)
        antes = conexao.token_expires_at

        async def _refresh(_t):
            return {"access_token": "novo", "expires_in": 5_184_000}

        async def _subscribe(_t, _i):
            raise ig.InstagramApiError("indisponível", codigo=2, permanente=False)

        monkeypatch.setattr(ig, "refresh_long_lived_token", _refresh)
        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        assert await svc.refresh_connection(conexao) is True
        db.refresh(conexao)
        assert conexao.token_expires_at > antes, "a renovação valeu, ela é o que importa aqui"
        assert conexao.webhook_subscrito is False

    @pytest.mark.asyncio
    async def test_retentativa_manual_pelo_botao(self, db, svc, monkeypatch):
        conexao = _conexao(db)
        conexao.webhook_subscrito = False
        conexao.webhook_erro = "perfil privado"
        db.commit()

        async def _subscribe(_t, _i):
            return True

        monkeypatch.setattr(ig, "subscribe_account_to_webhooks", _subscribe)

        resposta = await svc.assinar_webhook_do_usuario(1)

        assert resposta.webhook_subscrito is True
        assert resposta.webhook_erro is None

    @pytest.mark.asyncio
    async def test_retentativa_sem_conexao_da_404(self, svc):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            await svc.assinar_webhook_do_usuario(1)
        assert exc.value.status_code == 404


class TestCamposInscritos:
    def test_assina_comments_e_messages(self):
        """Desde a automação de STORY (09/2026) assinamos os dois campos:
        `comments` (posts/reels) e `messages` (reply de story chega como DM —
        `instagram_business_manage_messages` já era aprovado desde o v1).
        O webhook descarta toda DM que não é reply de story."""
        assert ig.CAMPOS_WEBHOOK == ["comments", "messages"]


class TestForceReauth:
    def test_url_de_autorizacao_forca_novo_login(self, monkeypatch):
        """Sem `force_reauth`, a Meta não mostra tela de login se já houver sessão —
        e a aluna conectaria a conta que estiver logada no navegador, que pode não
        ser a dela."""
        from app.core.config import settings

        monkeypatch.setattr(settings, "INSTAGRAM_APP_ID", "123", raising=False)
        monkeypatch.setattr(settings, "INSTAGRAM_APP_SECRET", "abc", raising=False)

        url = ig.build_authorize_url("https://x/cb", "estado")

        assert "force_reauth=true" in url
        assert "client_id=123" in url
        assert "instagram_business_manage_comments" in url
