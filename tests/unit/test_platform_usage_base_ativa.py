"""
_base_ativa() (card "Usuárias ativas", item 6 da Rodada 5) contra um banco de
verdade — o denominador precisa vir de subscription_events (fresco), não de
subscriptions.is_active (revalidado preguiçosamente, e inexistente pra quem só
foi importado historicamente).
"""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.subscription_event import SubscriptionEvent
from app.models.user import User
from app.services.platform_usage_service import PlatformUsageService


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(type_, compiler, **kw):
    return "JSON"


def _nova_sessao():
    """BigInteger PK não ganha autoincrement automático do SQLite (só INTEGER
    puro ganha) — listener atribui id manualmente, como em produção o Postgres
    faz via BIGSERIAL."""
    engine = create_engine("sqlite://")
    SubscriptionEvent.__table__.create(engine)
    User.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()

    contador = [0]

    @event.listens_for(SubscriptionEvent, "before_insert")
    def _atribuir_id(mapper, connection, target):
        if target.id is None:
            contador[0] += 1
            target.id = contador[0]

    sessao._remover_listener = lambda: event.remove(SubscriptionEvent, "before_insert", _atribuir_id)
    return sessao


@pytest.fixture
def db():
    sessao = _nova_sessao()
    yield sessao
    sessao.close()
    sessao._remover_listener()


def _user(db, **kwargs):
    padrao = dict(email="a@example.com", name="A", hashed_password="x", is_admin=False, is_demo=False)
    padrao.update(kwargs)
    u = User(**padrao)
    db.add(u)
    db.flush()
    return u


def _evento(db, **kwargs):
    padrao = dict(
        event_type="import_subscription_active",
        has_access=True,
        access_until=datetime.now(timezone.utc) + timedelta(days=30),
        received_at=datetime.now(timezone.utc),
        raw_payload={},
        dedupe_key=f"k-{kwargs.get('user_id')}-{kwargs.get('customer_cpf')}",
    )
    padrao.update(kwargs)
    db.add(SubscriptionEvent(**padrao))
    db.commit()


def test_assinante_importado_sem_linha_em_subscriptions_conta_como_ativo(db):
    """Antes: _base_ativa() lia subscriptions.is_active — alguém só com
    subscription_events (caso do import histórico) nunca apareceria."""
    user = _user(db, email="karem@example.com")
    _evento(db, user_id=user.id, customer_cpf="111", customer_email=user.email)

    base = PlatformUsageService(db)._base_ativa()
    assert user.id in base


def test_cancelado_sem_acesso_nao_conta(db):
    user = _user(db, email="churned@example.com")
    _evento(
        db, user_id=user.id, customer_cpf="222", customer_email=user.email,
        event_type="subscription_canceled", has_access=False, access_until=None,
    )

    base = PlatformUsageService(db)._base_ativa()
    assert user.id not in base


def test_admin_excluido_mesmo_ativo(db):
    admin = _user(db, email="admin@example.com", is_admin=True)
    _evento(db, user_id=admin.id, customer_cpf="333", customer_email=admin.email)

    base = PlatformUsageService(db)._base_ativa()
    assert admin.id not in base


def test_evento_sem_user_id_nao_conta_no_denominador():
    """Quem não tem User local resolvido não consegue logar — não entra no
    denominador de 'usuárias ativas' (que é sobre quem PODE usar a plataforma)."""
    db = _nova_sessao()
    _evento(db, user_id=None, customer_cpf="444", customer_email="sem-conta@example.com")

    base = PlatformUsageService(db)._base_ativa()
    assert base == []
    db.close()
    db._remover_listener()


def test_contas_no_total_exclui_admin_e_demo(db):
    _user(db, email="real1@example.com")
    _user(db, email="real2@example.com")
    _user(db, email="admin@example.com", is_admin=True)
    _user(db, email="demo@example.com", is_demo=True)
    db.commit()

    assert PlatformUsageService(db)._contas_no_total() == 2
