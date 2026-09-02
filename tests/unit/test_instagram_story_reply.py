"""Automação em STORY — webhook `messages`, matching, dedupe e a DM de resposta.

Espelha o test_instagram_pipeline: pipeline real contra SQLite, cliente da Meta
trocado por dublê. O que se protege aqui:

- reply de story dispara a automação de story (e SÓ ela — automação de feed não
  pode responder DM, nem automação de story responder comentário);
- dedupe por mid e por pessoa+story;
- janela de 24h da mensageria (timestamp vem em MILISSEGUNDOS);
- DM comum/echo é descartada no webhook, antes de virar task.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from app.api.webhooks import instagram as webhook
from app.core.config import settings
from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    DM_ENVIADO,
    DM_EXPIRADO,
    ESCOPO_QUALQUER,
    ESCOPO_STORY_ESPECIFICO,
    ESCOPO_STORY_QUALQUER,
    EVENTO_STORY_REPLY,
    TRIGGER_PALAVRAS,
    TRIGGER_QUALQUER,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.services import instagram_login_client as ig
from app.services.instagram_comment_pipeline import InstagramCommentPipeline
from app.utils.text_normalize import normalizar_comentario

IG_USER_ID = "17841400000000000"
STORY_ID = "18000000000000001"
OUTRO_STORY_ID = "18000000000000002"
SEGREDO = "segredo-de-teste"


# --------------------------------------------------------------------------- #
#  Fixtures (mesmo padrão do test_instagram_pipeline)                          #
# --------------------------------------------------------------------------- #


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
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _token_criptografado() -> str:
    from app.core.encryption import encrypt_value

    return encrypt_value("token-de-teste")


@pytest.fixture
def conexao(db):
    c = InstagramConnection(
        user_id=1,
        ig_user_id=IG_USER_ID,
        ig_username="aluna",
        access_token=_token_criptografado(),
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=50),
        status=CONEXAO_ATIVA,
    )
    db.add(c)
    db.commit()
    return c


def _automacao(db, conexao, **kwargs) -> InstagramAutomation:
    palavras = kwargs.pop("palavras", ["QUERO"])
    padrao = dict(
        user_id=1,
        connection_id=conexao.id,
        nome="Story teste",
        escopo=ESCOPO_STORY_ESPECIFICO,
        media_id=STORY_ID,
        trigger_tipo=TRIGGER_PALAVRAS,
        palavras=[normalizar_comentario(p) for p in palavras],
        palavras_exibicao=list(palavras),
        resposta_publica_ativa=False,
        resposta_publica_variacoes=[],
        dm_texto="Aqui está o link: https://exemplo.com",
        status=AUTOMACAO_ATIVA,
    )
    padrao.update(kwargs)
    a = InstagramAutomation(**padrao)
    db.add(a)
    db.commit()
    return a


class ClienteFalso:
    def __init__(self):
        self.dms_story: list[dict] = []
        self.erro_na_dm: Exception | None = None

    async def send_story_reply_dm(
        self, token, ig_user_id, recipient_id, texto, link=None, botao_texto=None
    ):
        if self.erro_na_dm:
            raise self.erro_na_dm
        self.dms_story.append(
            {
                "recipient_id": recipient_id,
                "texto": texto,
                "link": link,
                "botao_texto": botao_texto,
            }
        )
        return {"message_id": f"msg-{recipient_id}"}


@pytest.fixture
def cliente(monkeypatch):
    falso = ClienteFalso()
    monkeypatch.setattr(ig, "send_story_reply_dm", falso.send_story_reply_dm)

    async def _sem_espera(self):
        return None

    monkeypatch.setattr(InstagramCommentPipeline, "_espacar_envio", _sem_espera)
    return falso


def _pipeline(db) -> InstagramCommentPipeline:
    return InstagramCommentPipeline(InstagramAutomationRepository(db))


def _reply(mid="mid-1", texto="quero", sender="9001", story_id=STORY_ID, ts=None):
    if ts is None:
        # Epoch em MILISSEGUNDOS, como a mensageria manda.
        ts = int(datetime.now(timezone.utc).timestamp() * 1000)
    return {
        "mid": mid,
        "sender_id": sender,
        "story_id": story_id,
        "text": texto,
        "timestamp": ts,
    }


def _rodar(db, evento) -> dict:
    return asyncio.run(_pipeline(db).processar_story_reply(IG_USER_ID, evento))


# --------------------------------------------------------------------------- #
#  Pipeline                                                                    #
# --------------------------------------------------------------------------- #


class TestPipelineStory:
    def test_reply_com_palavra_dispara_dm(self, db, conexao, cliente):
        _automacao(db, conexao)
        resultado = _rodar(db, _reply())
        assert resultado["status"] == "enviado"
        assert len(cliente.dms_story) == 1
        assert cliente.dms_story[0]["recipient_id"] == "9001"
        evento = db.query(InstagramEvent).one()
        assert evento.tipo == EVENTO_STORY_REPLY
        assert evento.dm_status == DM_ENVIADO
        assert evento.media_id == STORY_ID
        assert evento.reply_status == "nao_aplicavel"

    def test_story_especifico_ignora_outro_story(self, db, conexao, cliente):
        _automacao(db, conexao)
        resultado = _rodar(db, _reply(story_id=OUTRO_STORY_ID))
        assert resultado["status"] == "ignorado"
        assert cliente.dms_story == []

    def test_story_qualquer_cobre_todos(self, db, conexao, cliente):
        _automacao(db, conexao, escopo=ESCOPO_STORY_QUALQUER, media_id=None)
        assert _rodar(db, _reply(story_id=OUTRO_STORY_ID))["status"] == "enviado"

    def test_trigger_qualquer_dispara_sem_palavra(self, db, conexao, cliente):
        _automacao(db, conexao, trigger_tipo=TRIGGER_QUALQUER, palavras=[])
        assert _rodar(db, _reply(texto="🔥🔥"))["status"] == "enviado"

    def test_sem_match_registra_e_nao_envia(self, db, conexao, cliente):
        _automacao(db, conexao)
        resultado = _rodar(db, _reply(texto="parabéns"))
        assert resultado["status"] == "sem_match"
        assert cliente.dms_story == []
        assert db.query(InstagramEvent).one().tipo == EVENTO_STORY_REPLY

    def test_dedupe_por_mid(self, db, conexao, cliente):
        _automacao(db, conexao)
        assert _rodar(db, _reply(mid="mid-x"))["status"] == "enviado"
        assert _rodar(db, _reply(mid="mid-x"))["status"] == "duplicado"
        assert len(cliente.dms_story) == 1

    def test_dedupe_pessoa_no_mesmo_story(self, db, conexao, cliente):
        _automacao(db, conexao)
        assert _rodar(db, _reply(mid="mid-1", sender="9001"))["status"] == "enviado"
        resultado = _rodar(db, _reply(mid="mid-2", sender="9001"))
        assert resultado["status"] == "duplicado"
        assert len(cliente.dms_story) == 1

    def test_janela_24h_expira(self, db, conexao, cliente):
        _automacao(db, conexao)
        antigo = int((datetime.now(timezone.utc) - timedelta(hours=25)).timestamp() * 1000)
        resultado = _rodar(db, _reply(ts=antigo))
        assert resultado["status"] == "expirado"
        assert db.query(InstagramEvent).one().dm_status == DM_EXPIRADO
        assert cliente.dms_story == []

    def test_mid_longo_cabe_na_coluna(self, db, conexao, cliente):
        """O mid REAL da Meta tem ~180+ chars (estourou os 160 da migration 072
        no primeiro reply de produção — migration 073 alargou para 512).
        SQLite não valida length de VARCHAR, então este teste documenta a
        intenção; quem valida de verdade é o Postgres."""
        _automacao(db, conexao)
        mid = (
            "aWdfZAG1faXRlbToxOklHTWVzc2FnZAUlEOjE3ODQxNDAxMTIyODE1NTA3OjM0MDI4"
            "MjM2Njg0MTcxMDMwMTI0NDI1ODQzODQ5MTI0MjQ5ODc2MTozMjk4OTQzMjc1NDM4OT"
            "c2NTQzMjEwOTg3NjU0MzIxMDk4NzY1NDMyMTA5ODc2NTQzMjEwOTg3NjU0MzIxMDo"
        )
        assert len(mid) > 160
        assert _rodar(db, _reply(mid=mid))["status"] == "enviado"

    def test_automacao_de_feed_nao_responde_story(self, db, conexao, cliente):
        """Escopo 'qualquer' (feed) NÃO pode vazar para DM de story."""
        _automacao(db, conexao, escopo=ESCOPO_QUALQUER, media_id=None)
        resultado = _rodar(db, _reply())
        assert resultado["status"] == "ignorado"
        assert cliente.dms_story == []

    def test_automacao_de_story_nao_responde_comentario(self, db, conexao):
        """O caminho inverso: cobre_media de automação de story é sempre False."""
        automacao = _automacao(db, conexao)
        assert automacao.cobre_media(STORY_ID) is False

    def test_mensagem_da_propria_conta_e_ignorada(self, db, conexao, cliente):
        _automacao(db, conexao)
        resultado = _rodar(db, _reply(sender=IG_USER_ID))
        assert resultado["status"] == "ignorado"
        assert cliente.dms_story == []


# --------------------------------------------------------------------------- #
#  Webhook — o filtro que descarta o que não é reply de story                  #
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_APP_SECRET", SEGREDO, raising=False)


def _assinar(corpo: bytes) -> str:
    return "sha256=" + hmac.new(SEGREDO.encode(), corpo, hashlib.sha256).hexdigest()


def _request_com(corpo: bytes) -> Request:
    async def receive():
        return {"type": "http.request", "body": corpo, "more_body": False}

    scope = {"type": "http", "method": "POST", "headers": [], "client": ("1.2.3.4", 0)}
    return Request(scope, receive)


def _mensagem(message: dict, sender: str = "9001") -> dict:
    return {
        "object": "instagram",
        "entry": [
            {
                "id": IG_USER_ID,
                "time": 1756800000,
                "messaging": [
                    {
                        "sender": {"id": sender},
                        "recipient": {"id": IG_USER_ID},
                        "timestamp": 1756800000000,
                        "message": message,
                    }
                ],
            }
        ],
    }


class TestWebhookMessaging:
    @pytest.fixture(autouse=True)
    def _captura(self, monkeypatch):
        self.stories: list[tuple[str, dict]] = []
        self.comentarios: list[tuple[str, dict]] = []
        monkeypatch.setattr(
            webhook, "_enfileirar_story_reply", lambda ig_id, ev: self.stories.append((ig_id, ev))
        )
        monkeypatch.setattr(
            webhook, "_enfileirar", lambda ig_id, v: self.comentarios.append((ig_id, v))
        )

    def _post(self, payload: dict) -> dict:
        corpo = json.dumps(payload).encode()
        return asyncio.run(
            webhook.receber_webhook(_request_com(corpo), x_hub_signature_256=_assinar(corpo))
        )

    def test_reply_de_story_enfileira(self):
        resultado = self._post(
            _mensagem(
                {
                    "mid": "mid-abc",
                    "text": "quero",
                    "reply_to": {"story": {"id": STORY_ID, "url": "https://cdn/x.mp4"}},
                }
            )
        )
        assert resultado == {"status": "ok", "enfileirados": 1}
        assert self.stories[0][0] == IG_USER_ID
        assert self.stories[0][1]["story_id"] == STORY_ID
        assert self.stories[0][1]["mid"] == "mid-abc"
        assert self.stories[0][1]["sender_id"] == "9001"

    def test_dm_comum_e_descartada(self):
        resultado = self._post(_mensagem({"mid": "mid-dm", "text": "oi, tudo bem?"}))
        assert resultado == {"status": "ok", "enfileirados": 0}
        assert self.stories == []

    def test_echo_e_descartado(self):
        resultado = self._post(
            _mensagem(
                {
                    "mid": "mid-echo",
                    "text": "resposta nossa",
                    "is_echo": True,
                    "reply_to": {"story": {"id": STORY_ID}},
                }
            )
        )
        assert resultado == {"status": "ok", "enfileirados": 0}

    def test_mensagem_da_propria_conta_e_descartada(self):
        resultado = self._post(
            _mensagem(
                {"mid": "mid-self", "text": "x", "reply_to": {"story": {"id": STORY_ID}}},
                sender=IG_USER_ID,
            )
        )
        assert resultado == {"status": "ok", "enfileirados": 0}

    def test_comentario_continua_funcionando_no_mesmo_payload(self):
        payload = _mensagem(
            {"mid": "m1", "text": "quero", "reply_to": {"story": {"id": STORY_ID}}}
        )
        payload["entry"][0]["changes"] = [
            {"field": "comments", "value": {"id": "c1", "text": "quero"}}
        ]
        resultado = self._post(payload)
        assert resultado == {"status": "ok", "enfileirados": 2}
        assert len(self.stories) == 1
        assert len(self.comentarios) == 1
