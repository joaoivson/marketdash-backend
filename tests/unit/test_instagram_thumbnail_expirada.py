"""A thumbnail guardada expira — e a tela não pode pedir URL morta ao CDN.

`media_thumbnail_url` é um retrato do instante da criação da automação. A URL do
CDN do Instagram é assinada e traz a validade em `oe=` (epoch hexadecimal);
vencida, `scontent-*.cdninstagram.com` devolve 403 direto para o navegador — sem
passar pela nossa API, sem log nosso. Foi o que a aluna viu em 16/09/2026, com
URLs vencidas havia de 3 a 9 dias.
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
    ESCOPO_POST_ESPECIFICO,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.services import instagram_automation_service as mod
from app.services.instagram_automation_service import InstagramAutomationService
from app.services.instagram_media_url import expirada, validade


def _url(oe_epoch: int) -> str:
    return (
        "https://scontent-gru1-1.cdninstagram.com/v/t51.29350-15/abc.jpg"
        f"?stp=dst-jpg&_nc_ht=scontent-gru1-1&oh=00_AfQxYz&oe={oe_epoch:X}"
    )


# --------------------------------------------------------------------------- #
#  O helper — sem rede                                                         #
# --------------------------------------------------------------------------- #


def test_validade_le_o_parametro_oe_em_hexadecimal():
    # 0x6AA73925 = 14/09/2026 00:00 UTC, uma das URLs do incidente.
    assert validade(_url(0x6AA73925)) == datetime(2026, 9, 14, 0, 0, 37, tzinfo=timezone.utc)


def test_url_com_validade_no_passado_esta_expirada():
    passado = datetime.now(timezone.utc) - timedelta(days=3)
    assert expirada(_url(int(passado.timestamp()))) is True


def test_url_com_validade_folgada_nao_esta_expirada():
    futuro = datetime.now(timezone.utc) + timedelta(days=2)
    assert expirada(_url(int(futuro.timestamp()))) is False


def test_url_sem_oe_nunca_e_tratada_como_expirada():
    """"Não sei dizer" não pode virar "está quebrada" — apagar URL boa deixaria
    a tela sem thumbnail à toa."""
    assert expirada("https://scontent-gru1-1.cdninstagram.com/v/t51/abc.jpg") is False
    assert expirada("oe=nao-e-hex") is False
    assert expirada(None) is False
    assert expirada("") is False


# --------------------------------------------------------------------------- #
#  A listagem                                                                  #
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


def _automacao(db, thumb: str) -> InstagramAutomation:
    conexao = db.query(InstagramConnection).first()
    a = InstagramAutomation(
        user_id=1,
        connection_id=conexao.id,
        nome="Post do cupom",
        escopo=ESCOPO_POST_ESPECIFICO,
        media_id="17900000000000000",
        media_thumbnail_url=thumb,
        palavras=["quero"],
        palavras_exibicao=["QUERO"],
        dm_texto="oi",
        status=AUTOMACAO_ATIVA,
    )
    db.add(a)
    db.commit()
    return a


@pytest.mark.asyncio
async def test_listar_renova_thumbnail_vencida_pela_graph_api(db, monkeypatch):
    vencida = _url(int((datetime.now(timezone.utc) - timedelta(days=9)).timestamp()))
    automacao = _automacao(db, vencida)
    nova = _url(int((datetime.now(timezone.utc) + timedelta(days=2)).timestamp()))

    async def fake_get_media(token, media_id):
        assert media_id == "17900000000000000"
        return {"id": media_id, "thumbnail_url": nova}

    monkeypatch.setattr(mod.ig, "get_media", fake_get_media)

    svc = InstagramAutomationService(InstagramAutomationRepository(db))
    resposta = await svc.listar(1)

    assert resposta[0].media_thumbnail_url == nova
    db.refresh(automacao)
    assert automacao.media_thumbnail_url == nova, "a renovação tem que persistir"


@pytest.mark.asyncio
async def test_listar_apaga_a_url_quando_a_meta_falha(db, monkeypatch):
    """Falhou a renovação, o campo fica vazio — nunca a URL morta.

    Ausência tem placeholder na tela; quebrada só tem o 403 no console.
    """
    vencida = _url(int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp()))
    automacao = _automacao(db, vencida)

    async def fake_get_media(token, media_id):
        raise mod.ig.InstagramApiError("post apagado", permanente=True)

    monkeypatch.setattr(mod.ig, "get_media", fake_get_media)

    svc = InstagramAutomationService(InstagramAutomationRepository(db))
    resposta = await svc.listar(1)

    assert resposta[0].media_thumbnail_url is None
    db.refresh(automacao)
    assert automacao.media_thumbnail_url is None


@pytest.mark.asyncio
async def test_listar_nao_chama_a_meta_quando_a_url_ainda_vale(db, monkeypatch):
    """O `oe=` é lido localmente: URL boa não gasta cota nem latência."""
    boa = _url(int((datetime.now(timezone.utc) + timedelta(days=5)).timestamp()))
    _automacao(db, boa)

    async def nao_deve_chamar(token, media_id):  # pragma: no cover
        raise AssertionError("renovou uma thumbnail que ainda valia")

    monkeypatch.setattr(mod.ig, "get_media", nao_deve_chamar)

    svc = InstagramAutomationService(InstagramAutomationRepository(db))
    resposta = await svc.listar(1)

    assert resposta[0].media_thumbnail_url == boa


@pytest.mark.asyncio
async def test_erro_da_meta_nunca_pausa_as_automacoes(db, monkeypatch):
    """Código 190 aqui NÃO pode acionar `handle_token_invalido`.

    Ele pausa todas as automações da conta — efeito que não pode nascer de
    alguém apenas abrir a tela de listagem. Quem marca o token é o envio real.
    """
    vencida = _url(int((datetime.now(timezone.utc) - timedelta(days=3)).timestamp()))
    automacao = _automacao(db, vencida)

    async def token_invalido(token, media_id):
        raise mod.ig.InstagramApiError("token recusado", codigo=190, permanente=True)

    monkeypatch.setattr(mod.ig, "get_media", token_invalido)

    svc = InstagramAutomationService(InstagramAutomationRepository(db))
    await svc.listar(1)

    db.refresh(automacao)
    assert automacao.status == AUTOMACAO_ATIVA
    assert db.query(InstagramConnection).first().status == CONEXAO_ATIVA
