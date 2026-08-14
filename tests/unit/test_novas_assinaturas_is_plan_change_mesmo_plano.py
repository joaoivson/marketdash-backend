"""new_subscriptions() não pode perder assinante cuja ÚNICA cobrança do
mês está marcada is_plan_change=True mesmo sem trocar de plano de
verdade — caso real de produção: Deivit Rafael Ferreira Martins,
31/07/2026, pagou Essencial e cancelou 2 minutos depois (22:05→22:07),
MESMO plano do início ao fim, mas os 2 eventos vieram marcados
is_plan_change=True (herança de heurística de outra rodada, não é uma
troca de plano real). O filtro antigo excluía esse tipo de assinante da
contagem de "novas assinaturas do mês", mesmo sendo genuinamente a 1ª
cobrança histórica da pessoa — produção mostrava 6 novas em julho/2026
quando o correto (1ª cobrança histórica por assinante, sem filtrar por
is_plan_change) é 7.

Banco SQLite real (não mock) — a função faz duas queries distintas com
filtros diferentes; um mock genérico de `db.query(...).filter(...).all()`
não distingue as duas chamadas e deixa passar um teste vazio (o bug real
não é reproduzido). Mesmo padrão de `test_platform_usage_base_ativa.py`.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.subscription_event import SubscriptionEvent
from app.services.admin_metrics_service import AdminMetricsService


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(type_, compiler, **kw):
    return "JSON"


def _nova_sessao():
    """BigInteger PK não ganha autoincrement automático do SQLite (só INTEGER
    puro ganha) — listener atribui id manualmente, como em produção o Postgres
    faz via BIGSERIAL."""
    engine = create_engine("sqlite://")
    SubscriptionEvent.__table__.create(engine)
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


def _evento(db, **kwargs):
    padrao = dict(
        event_type="order_approved",
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        is_plan_change=False,
        has_access=True,
        raw_payload={},
    )
    padrao.update(kwargs)
    padrao.setdefault("dedupe_key", f"k-{kwargs.get('subscription_id')}-{kwargs.get('received_at')}")
    db.add(SubscriptionEvent(**padrao))
    db.commit()


def test_conta_assinante_cuja_unica_cobranca_do_mes_tem_is_plan_change_true(db):
    _evento(db, subscription_id="sub-normal", customer_email="a@example.com")
    _evento(
        db, subscription_id="sub-deivit", customer_email="deivit@example.com",
        received_at=datetime(2026, 7, 31, 22, 5, tzinfo=timezone.utc), is_plan_change=True,
    )

    svc = AdminMetricsService(db)
    assert svc.new_subscriptions(2026, 7) == 2


def test_nao_conta_assinante_cuja_1a_cobranca_e_de_outro_mes(db):
    # a pessoa pagou em junho pela 1ª vez e renovou em julho — julho não
    # é o mês de entrada dela, mesmo tendo um evento pago dentro do range.
    _evento(
        db, subscription_id="sub-x", customer_email="b@example.com",
        received_at=datetime(2026, 6, 15, tzinfo=timezone.utc),
        dedupe_key="junho",
    )
    _evento(
        db, subscription_id="sub-x", customer_email="b@example.com",
        received_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        dedupe_key="julho-renovacao",
    )

    svc = AdminMetricsService(db)
    assert svc.new_subscriptions(2026, 7) == 0
