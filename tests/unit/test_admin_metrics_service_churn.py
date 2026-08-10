"""
churn_for_month contra um banco de verdade (SQLite em memória).

Cobre a exclusão de "Cancelado pelo produtor" (ajuste administrativo, não
churn real) — regra nova desta rodada, motivada pelo import histórico Kiwify
(5 dos 16 cancelamentos importados são ajustes do Luiz, não saída de cliente).
"""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.subscription_event import SubscriptionEvent
from app.services.admin_metrics_service import AdminMetricsService


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SubscriptionEvent.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


_next_id = [0]


def _evento(db, **kwargs):
    _next_id[0] += 1
    padrao = dict(
        id=_next_id[0],
        event_type="order_approved",
        customer_cpf="000",
        subscription_id=None,
        has_access=True,
        access_until=None,
        received_at=datetime(2026, 7, 15, tzinfo=timezone.utc),
        raw_payload={},
        dedupe_key=f"k-{_next_id[0]}",
        is_plan_change=False,
    )
    padrao.update(kwargs)
    db.add(SubscriptionEvent(**padrao))
    db.commit()


def test_cancelado_pelo_produtor_nao_conta_churn(db):
    # Ativa desde antes de julho, pra entrar no denominador de "início do mês".
    _evento(db, event_type="order_approved", customer_cpf="ajuste",
            received_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            has_access=True, access_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
    _evento(db, event_type="subscription_canceled", customer_cpf="ajuste",
            received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            cancel_reason="Cancelado pelo produtor", has_access=False, access_until=None)

    churn = AdminMetricsService(db).churn_for_month(2026, 7)
    assert churn["count"] == 0


def test_cancelado_pelo_comprador_conta_churn_normal(db):
    _evento(db, event_type="order_approved", customer_cpf="real",
            received_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            has_access=True, access_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
    _evento(db, event_type="subscription_canceled", customer_cpf="real",
            received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            cancel_reason="Cancelado pelo comprador", has_access=False, access_until=None)

    churn = AdminMetricsService(db).churn_for_month(2026, 7)
    assert churn["count"] == 1


def test_mistura_conta_so_o_real(db):
    for cpf, motivo in [("ajuste1", "Cancelado pelo produtor"),
                        ("ajuste2", "Cancelado pelo produtor"),
                        ("real1", "Pagamento não efetuado")]:
        _evento(db, event_type="order_approved", customer_cpf=cpf,
                received_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                has_access=True, access_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
        _evento(db, event_type="subscription_canceled", customer_cpf=cpf,
                received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
                cancel_reason=motivo, has_access=False, access_until=None)

    churn = AdminMetricsService(db).churn_for_month(2026, 7)
    assert churn["count"] == 1
