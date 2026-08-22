"""Testes de aceite §9 — dedupe, janela, volume, rotação e ordem de envio.

Roda o pipeline de verdade contra SQLite, com o cliente da Meta trocado por um
dublê que registra as chamadas. O que se verifica aqui é a lógica que protege a
aluna: não mandar duas vezes, não mentir no comentário público, não estourar o
teto da Meta.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    DM_ENVIADO,
    DM_EXPIRADO,
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_QUALQUER,
    TRIGGER_PALAVRAS,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.services import instagram_login_client as ig
from app.services.instagram_comment_pipeline import InstagramCommentPipeline
from app.utils.text_normalize import normalizar_comentario

IG_USER_ID = "17841400000000000"
MEDIA_ID = "18000000000000000"
OUTRO_MEDIA_ID = "18999999999999999"


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


@pytest.fixture
def conexao(db):
    c = InstagramConnection(
        user_id=1,
        ig_user_id=IG_USER_ID,
        ig_username="aluna",
        # O token é descriptografado com Fernet; o dublê do cliente nunca o usa,
        # mas `token_de` precisa conseguir decodificar.
        access_token=_token_criptografado(),
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=50),
        status=CONEXAO_ATIVA,
    )
    db.add(c)
    db.commit()
    return c


def _token_criptografado() -> str:
    from app.core.encryption import encrypt_value

    return encrypt_value("token-de-teste")


def _automacao(db, conexao, **kwargs) -> InstagramAutomation:
    palavras = kwargs.pop("palavras", ["QUERO"])
    padrao = dict(
        user_id=1,
        connection_id=conexao.id,
        nome="Teste",
        escopo=ESCOPO_POST_ESPECIFICO,
        media_id=MEDIA_ID,
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
    """Dublê do cliente da Meta. Registra chamadas e permite programar falhas."""

    def __init__(self):
        self.dms: list[tuple[str, str]] = []
        # Rodada 2: guarda link e título do botão para os testes conferirem o
        # formato enviado, não só o texto.
        self.dms_completas: list[dict] = []
        self.replies: list[tuple[str, str]] = []
        self.erro_na_dm: Exception | None = None
        self.erro_no_reply: Exception | None = None

    async def send_private_reply(
        self, token, ig_user_id, comment_id, texto, link=None, botao_texto=None
    ):
        if self.erro_na_dm:
            raise self.erro_na_dm
        self.dms.append((comment_id, texto))
        self.dms_completas.append(
            {"comment_id": comment_id, "texto": texto, "link": link, "botao_texto": botao_texto}
        )
        return {"message_id": f"msg-{comment_id}"}

    async def reply_to_comment(self, token, comment_id, texto):
        if self.erro_no_reply:
            raise self.erro_no_reply
        self.replies.append((comment_id, texto))
        return {"id": f"reply-{comment_id}"}

    async def get_media(self, token, media_id):
        return {"id": media_id, "timestamp": "2026-08-19T10:00:00+0000"}


@pytest.fixture
def cliente(monkeypatch):
    falso = ClienteFalso()
    monkeypatch.setattr(ig, "send_private_reply", falso.send_private_reply)
    monkeypatch.setattr(ig, "reply_to_comment", falso.reply_to_comment)
    monkeypatch.setattr(ig, "get_media", falso.get_media)
    # O espaçamento de 1/5s por envio existe pra não parecer bot em produção;
    # nos testes ele só somaria 20s de espera sem verificar nada.
    async def _sem_espera(self):
        return None

    monkeypatch.setattr(InstagramCommentPipeline, "_espacar_envio", _sem_espera)
    return falso


def _comentario(comment_id: str, texto="quero", commenter="9001", media_id=MEDIA_ID, ts=None):
    return {
        "id": comment_id,
        "text": texto,
        "from": {"id": commenter, "username": f"user{commenter}"},
        "media": {"id": media_id},
        "timestamp": (ts or datetime.now(timezone.utc)).isoformat(),
    }


async def _processar(db, valor):
    return await InstagramCommentPipeline(InstagramAutomationRepository(db)).processar_comentario(
        IG_USER_ID, valor
    )


# --------------------------------------------------------------------------- #
#  §9 Dedupe                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_1_pessoa_comenta_recebe_um_direct(db, conexao, cliente):
    _automacao(db, conexao)
    resultado = await _processar(db, _comentario("c1"))
    assert resultado["status"] == "enviado"
    assert len(cliente.dms) == 1


@pytest.mark.asyncio
async def test_2_mesma_pessoa_comenta_de_novo_no_mesmo_post_nao_recebe_segundo(db, conexao, cliente):
    _automacao(db, conexao)
    await _processar(db, _comentario("c1"))
    resultado = await _processar(db, _comentario("c2"))

    assert resultado["status"] == "duplicado"
    assert len(cliente.dms) == 1, "só o primeiro comentário pode gerar direct"
    evento = db.query(InstagramEvent).filter(InstagramEvent.comment_id == "c2").one()
    assert evento.dm_status == "duplicado"


@pytest.mark.asyncio
async def test_3_mesma_pessoa_em_outro_post_recebe_direct(db, conexao, cliente):
    """Automação nova, post diferente: é outra oferta, a pessoa recebe de novo."""
    _automacao(db, conexao, nome="Post A")
    _automacao(db, conexao, nome="Post B", media_id=OUTRO_MEDIA_ID)

    await _processar(db, _comentario("c1", media_id=MEDIA_ID))
    resultado = await _processar(db, _comentario("c2", media_id=OUTRO_MEDIA_ID))

    assert resultado["status"] == "enviado"
    assert len(cliente.dms) == 2


@pytest.mark.asyncio
async def test_4_comentario_da_propria_aluna_nao_dispara_nada(db, conexao, cliente):
    """Sem isso, a resposta pública que NÓS postamos dispararia a automação em loop."""
    _automacao(db, conexao)
    resultado = await _processar(db, _comentario("c1", commenter=IG_USER_ID))

    assert resultado["status"] == "ignorado"
    assert cliente.dms == []
    assert db.query(InstagramEvent).count() == 0


@pytest.mark.asyncio
async def test_mesmo_comment_id_reentregue_pela_meta_nao_duplica(db, conexao, cliente):
    _automacao(db, conexao)
    await _processar(db, _comentario("c1"))
    resultado = await _processar(db, _comentario("c1"))
    assert resultado["status"] == "duplicado"
    assert len(cliente.dms) == 1


# --------------------------------------------------------------------------- #
#  §9 Janela                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_5_comentario_de_8_dias_atras_expira_sem_chamar_a_meta(db, conexao, cliente):
    _automacao(db, conexao)
    antigo = datetime.now(timezone.utc) - timedelta(days=8)
    resultado = await _processar(db, _comentario("c1", ts=antigo))

    assert resultado["status"] == "expirado"
    assert cliente.dms == [], "nenhuma chamada de envio pode ser disparada"
    evento = db.query(InstagramEvent).filter(InstagramEvent.comment_id == "c1").one()
    assert evento.dm_status == DM_EXPIRADO
    assert evento.erro_codigo == "JANELA_7_DIAS"


@pytest.mark.asyncio
async def test_comentario_de_6_dias_ainda_dentro_da_janela(db, conexao, cliente):
    _automacao(db, conexao)
    quase = datetime.now(timezone.utc) - timedelta(days=6, hours=23)
    resultado = await _processar(db, _comentario("c1", ts=quase))
    assert resultado["status"] == "enviado"


# --------------------------------------------------------------------------- #
#  §9 Volume                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_6_cem_comentarios_geram_cem_directs_sem_duplicar(db, conexao, cliente):
    _automacao(db, conexao)
    for i in range(100):
        resultado = await _processar(db, _comentario(f"c{i}", commenter=str(10_000 + i)))
        assert resultado["status"] == "enviado", f"comentário {i} não foi enviado"

    assert len(cliente.dms) == 100
    assert len({c for c, _ in cliente.dms}) == 100, "nenhum comment_id repetido"
    assert db.query(InstagramEvent).filter(InstagramEvent.dm_status == DM_ENVIADO).count() == 100


@pytest.mark.asyncio
async def test_teto_horario_segura_o_envio_em_vez_de_estourar(db, conexao, cliente, monkeypatch):
    """Passando do teto, o pipeline ADIA — não descarta nem chama a Meta."""
    from app.core.config import settings
    from app.services.instagram_comment_pipeline import ThrottleExcedido

    monkeypatch.setattr(settings, "INSTAGRAM_MAX_PRIVATE_REPLIES_HORA", 3, raising=False)
    _automacao(db, conexao)
    for i in range(3):
        await _processar(db, _comentario(f"c{i}", commenter=str(700 + i)))

    with pytest.raises(ThrottleExcedido) as exc:
        await _processar(db, _comentario("c99", commenter="799"))

    assert exc.value.segundos_para_tentar > 0
    assert len(cliente.dms) == 3, "o 4º não pode chegar na Meta"
    # E o comment_id NÃO pode ficar reservado, senão nunca seria reprocessado.
    assert db.query(InstagramEvent).filter(InstagramEvent.comment_id == "c99").count() == 0


# --------------------------------------------------------------------------- #
#  §9 Resposta pública                                                         #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_7_tres_variacoes_em_seis_comentarios_aparecem_duas_vezes_cada(db, conexao, cliente):
    variacoes = ["Te mandei no direct!", "Já foi pro seu direct", "Enviado! Corre lá no direct"]
    _automacao(db, conexao, resposta_publica_ativa=True, resposta_publica_variacoes=variacoes)

    for i in range(6):
        await _processar(db, _comentario(f"c{i}", commenter=str(200 + i)))

    usados = [texto for _, texto in cliente.replies]
    assert len(usados) == 6
    for variacao in variacoes:
        assert usados.count(variacao) == 2, f"'{variacao}' apareceu {usados.count(variacao)}x"
    # Rotação, não sorteio: a ordem se repete.
    assert usados[:3] == variacoes
    assert usados[3:] == variacoes


@pytest.mark.asyncio
async def test_8_se_a_dm_falha_a_resposta_publica_nao_e_enviada(db, conexao, cliente):
    """Responder 'te mandei no direct' sem ter mandado é mentira visível no post."""
    _automacao(
        db, conexao, resposta_publica_ativa=True, resposta_publica_variacoes=["Te mandei no direct!"]
    )
    cliente.erro_na_dm = ig.InstagramApiError(
        "janela expirada", codigo=100, subcodigo=2534014, permanente=True
    )

    resultado = await _processar(db, _comentario("c1"))

    assert resultado["status"] == "falhou"
    assert cliente.replies == [], "nada pode ser publicado no comentário"
    evento = db.query(InstagramEvent).filter(InstagramEvent.comment_id == "c1").one()
    assert evento.dm_status == "falhou"
    assert evento.reply_status == "pulado"


@pytest.mark.asyncio
async def test_resposta_publica_falhando_nao_desfaz_a_dm(db, conexao, cliente):
    _automacao(db, conexao, resposta_publica_ativa=True, resposta_publica_variacoes=["ok"])
    cliente.erro_no_reply = ig.InstagramApiError("comentário apagado", codigo=100, permanente=True)

    resultado = await _processar(db, _comentario("c1"))

    assert resultado["status"] == "enviado"
    evento = db.query(InstagramEvent).filter(InstagramEvent.comment_id == "c1").one()
    assert evento.dm_status == DM_ENVIADO
    assert evento.reply_status == "falhou"


# --------------------------------------------------------------------------- #
#  Escopo                                                                      #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_escopo_qualquer_publicacao_cobre_post_novo(db, conexao, cliente):
    _automacao(db, conexao, escopo=ESCOPO_QUALQUER, media_id=None)
    resultado = await _processar(db, _comentario("c1", media_id="19111111111111111"))
    assert resultado["status"] == "enviado"


@pytest.mark.asyncio
async def test_automacao_de_outro_post_nao_responde(db, conexao, cliente):
    _automacao(db, conexao, media_id=MEDIA_ID)
    resultado = await _processar(db, _comentario("c1", media_id=OUTRO_MEDIA_ID))
    assert resultado["status"] == "ignorado"
    assert cliente.dms == []


@pytest.mark.asyncio
async def test_automacao_pausada_nao_responde(db, conexao, cliente):
    _automacao(db, conexao, status="pausada")
    resultado = await _processar(db, _comentario("c1"))
    assert resultado["status"] == "ignorado"
    assert cliente.dms == []


@pytest.mark.asyncio
async def test_comentario_sem_palavra_chave_registra_sem_match(db, conexao, cliente):
    _automacao(db, conexao, palavras=["QUERO"])
    resultado = await _processar(db, _comentario("c1", texto="que lindo"))
    assert resultado["status"] == "sem_match"
    assert cliente.dms == []
    evento = db.query(InstagramEvent).filter(InstagramEvent.comment_id == "c1").one()
    assert evento.dm_status == "sem_match"


@pytest.mark.asyncio
async def test_token_recusado_no_envio_pausa_as_automacoes(db, conexao, cliente):
    """Sem isso, a aluna só descobre pelo aluno reclamando que não chegou link."""
    automacao = _automacao(db, conexao)
    cliente.erro_na_dm = ig.InstagramApiError("token inválido", codigo=190, permanente=True)

    await _processar(db, _comentario("c1"))

    db.refresh(automacao)
    db.refresh(conexao)
    assert automacao.status == "pausada"
    assert conexao.status == "expirado"
