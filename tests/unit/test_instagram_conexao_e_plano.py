"""Testes de aceite §9 — conexão (deauthorize, renovação, conta pessoal) e plano."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.plans import MAX_ONLY_MENUS, plan_allows_menu
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
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _conexao(db, dias_para_vencer=50) -> InstagramConnection:
    from app.core.encryption import encrypt_value

    c = InstagramConnection(
        user_id=1,
        ig_user_id=IG_USER_ID,
        ig_username="aluna",
        access_token=encrypt_value("token-longo"),
        token_expires_at=datetime.now(timezone.utc) + timedelta(days=dias_para_vencer),
        status=CONEXAO_ATIVA,
    )
    db.add(c)
    db.commit()
    a = InstagramAutomation(
        user_id=1,
        connection_id=c.id,
        nome="Teste",
        media_id="1800",
        palavras=["quero"],
        palavras_exibicao=["QUERO"],
        resposta_publica_variacoes=[],
        dm_texto="link",
        status=AUTOMACAO_ATIVA,
    )
    db.add(a)
    db.commit()
    return c


def _service(db) -> InstagramConnectionService:
    return InstagramConnectionService(InstagramAutomationRepository(db))


# --------------------------------------------------------------------------- #
#  §9 item 9 — deauthorize                                                     #
# --------------------------------------------------------------------------- #


def test_9_deauthorize_pausa_automacoes_e_marca_revogado(db):
    conexao = _conexao(db)
    assert _service(db).handle_deauthorize(IG_USER_ID) is True

    db.refresh(conexao)
    assert conexao.status == "revogado"
    assert db.query(InstagramAutomation).one().status == "pausada"


def test_deauthorize_de_conta_desconhecida_nao_quebra(db):
    assert _service(db).handle_deauthorize("99999999") is False


def test_deauthorize_nao_apaga_nada(db):
    """Pausar, não apagar: reconectando, a aluna reencontra tudo como deixou."""
    _conexao(db)
    _service(db).handle_deauthorize(IG_USER_ID)
    assert db.query(InstagramAutomation).count() == 1
    assert db.query(InstagramConnection).count() == 1


def test_data_deletion_apaga_conexao_e_automacoes(db):
    _conexao(db)
    assert _service(db).handle_data_deletion(IG_USER_ID) == 1
    assert db.query(InstagramConnection).count() == 0


# --------------------------------------------------------------------------- #
#  §9 item 10 — renovação de token                                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_10_token_a_nove_dias_do_vencimento_e_renovado(db, monkeypatch):
    conexao = _conexao(db, dias_para_vencer=9)
    antes = conexao.token_expires_at

    async def _refresh(_token):
        return {"access_token": "token-novo", "expires_in": 5_184_000}  # 60 dias

    monkeypatch.setattr(ig, "refresh_long_lived_token", _refresh)

    assert await _service(db).refresh_connection(conexao) is True
    db.refresh(conexao)
    assert conexao.token_expires_at > antes + timedelta(days=45)
    assert conexao.last_refreshed_at is not None
    assert conexao.status == CONEXAO_ATIVA


def test_selecao_do_cron_pega_so_quem_vence_em_menos_de_dez_dias(db):
    from app.core.encryption import encrypt_value

    _conexao(db, dias_para_vencer=9)  # user 1 — entra
    db.add(
        InstagramConnection(
            user_id=2,
            ig_user_id="222",
            access_token=encrypt_value("t"),
            token_expires_at=datetime.now(timezone.utc) + timedelta(days=40),
            status=CONEXAO_ATIVA,
        )
    )
    db.commit()

    pendentes = InstagramAutomationRepository(db).connections_needing_refresh(10)
    assert [c.user_id for c in pendentes] == [1]


def test_conexao_sem_validade_registrada_entra_na_renovacao(db):
    """Token sem `expires_at` não pode ficar de fora — venceria em silêncio."""
    from app.core.encryption import encrypt_value

    db.add(
        InstagramConnection(
            user_id=1,
            ig_user_id="333",
            access_token=encrypt_value("t"),
            token_expires_at=None,
            status=CONEXAO_ATIVA,
        )
    )
    db.commit()
    assert len(InstagramAutomationRepository(db).connections_needing_refresh(10)) == 1


@pytest.mark.asyncio
async def test_renovacao_impossivel_pausa_automacoes_e_marca_expirado(db, monkeypatch):
    conexao = _conexao(db, dias_para_vencer=1)

    async def _refresh(_token):
        raise ig.InstagramApiError("token expirado", codigo=190, permanente=True)

    monkeypatch.setattr(ig, "refresh_long_lived_token", _refresh)

    assert await _service(db).refresh_connection(conexao) is False
    db.refresh(conexao)
    assert conexao.status == "expirado"
    assert db.query(InstagramAutomation).one().status == "pausada"


def test_token_vencido_reflete_no_status_e_pausa(db):
    conexao = _conexao(db, dias_para_vencer=-1)
    status = _service(db).get_status(1)
    assert status is not None and status.status == "expirado"
    db.refresh(conexao)
    assert db.query(InstagramAutomation).one().status == "pausada"


# --------------------------------------------------------------------------- #
#  §9 item 11 — conta pessoal                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_11_conta_pessoal_recebe_erro_claro_sem_stack_trace(db, monkeypatch):
    async def _code(_c, _r):
        return {"access_token": "curto"}

    async def _longo(_t):
        return {"access_token": "longo", "expires_in": 5_184_000}

    async def _me(_t):
        return {"user_id": "555", "username": "pessoal", "account_type": "PERSONAL"}

    monkeypatch.setattr(ig, "exchange_code_for_short_token", _code)
    monkeypatch.setattr(ig, "exchange_for_long_lived_token", _longo)
    monkeypatch.setattr(ig, "get_me", _me)

    with pytest.raises(HTTPException) as exc:
        await _service(db).handle_oauth_callback(1, "code", "https://x/callback")

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "CONTA_NAO_PROFISSIONAL"
    assert "Profissional" in exc.value.detail["message"]
    assert db.query(InstagramConnection).count() == 0


@pytest.mark.asyncio
async def test_conta_criador_conecta_normalmente(db, monkeypatch):
    async def _code(_c, _r):
        return {"access_token": "curto"}

    async def _longo(_t):
        return {"access_token": "longo", "expires_in": 5_184_000}

    async def _me(_t):
        return {"user_id": "777", "username": "criadora", "account_type": "MEDIA_CREATOR"}

    monkeypatch.setattr(ig, "exchange_code_for_short_token", _code)
    monkeypatch.setattr(ig, "exchange_for_long_lived_token", _longo)
    monkeypatch.setattr(ig, "get_me", _me)

    resposta = await _service(db).handle_oauth_callback(1, "code", "https://x/callback")
    assert resposta.ig_username == "criadora"
    assert resposta.status == CONEXAO_ATIVA


@pytest.mark.asyncio
async def test_conta_ja_conectada_em_outro_usuario_e_recusada(db, monkeypatch):
    """O webhook chega identificado pelo ig_user_id — com dois donos não haveria
    como decidir de quem é o comentário."""
    _conexao(db)
    async def _code(_c, _r):
        return {"access_token": "curto"}

    async def _longo(_t):
        return {"access_token": "longo", "expires_in": 5_184_000}

    async def _me(_t):
        return {"user_id": IG_USER_ID, "username": "aluna", "account_type": "BUSINESS"}

    monkeypatch.setattr(ig, "exchange_code_for_short_token", _code)
    monkeypatch.setattr(ig, "exchange_for_long_lived_token", _longo)
    monkeypatch.setattr(ig, "get_me", _me)

    with pytest.raises(HTTPException) as exc:
        await _service(db).handle_oauth_callback(2, "code", "https://x/callback")
    assert exc.value.detail["code"] == "INSTAGRAM_JA_CONECTADO"


# --------------------------------------------------------------------------- #
#  §9 item 12 — plano                                                          #
# --------------------------------------------------------------------------- #


def _tem_gate_max(rota) -> bool:
    """A rota declara `Depends(exige_plano_max)` em algum ponto da árvore?"""
    from app.api.v1.routes import instagram as rotas

    def _varrer(dependant) -> bool:
        for sub in dependant.dependencies:
            if sub.call is rotas.exige_plano_max or _varrer(sub):
                return True
        return False

    return _varrer(rota.dependant)


class TestGateDePlano:
    def test_12_essencial_e_pro_nao_tem_acesso_max_tem(self):
        assert plan_allows_menu("essencial", "automacoes") is False
        assert plan_allows_menu("pro", "automacoes") is False
        assert plan_allows_menu("max", "automacoes") is True

    def test_menu_esta_marcado_como_exclusivo_do_max(self):
        assert "automacoes" in MAX_ONLY_MENUS

    def test_rotas_de_automacao_exigem_plano_max_no_backend(self):
        """Bloqueio no BACKEND, não só na UI: bater direto na URL tem que dar 403.

        Inspeciona a árvore de dependências das rotas — é o que impede uma rota
        nova de nascer sem gate por esquecimento.
        """
        from app.api.v1.routes import instagram as rotas

        precisam_gate = [
            r for r in rotas.router.routes if "/automations" in r.path or r.path == "/media"
        ]
        assert precisam_gate, "nenhuma rota de automação encontrada"

        sem_gate = [r.path for r in precisam_gate if not _tem_gate_max(r)]
        assert sem_gate == [], f"rotas de automação sem gate de plano: {sem_gate}"

    def test_conexao_fica_fora_do_gate(self):
        """Se a assinatura cair de MAX, a aluna ainda precisa ver e remover a conexão."""
        from app.api.v1.routes import instagram as rotas

        conexao = [r for r in rotas.router.routes if r.path == "/connection"]
        assert conexao, "rota /connection não encontrada"
        assert not any(_tem_gate_max(r) for r in conexao)
