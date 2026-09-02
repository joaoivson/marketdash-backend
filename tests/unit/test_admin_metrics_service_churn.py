"""
churn_for_month contra um banco de verdade (SQLite em memória).

Cobre a regra do "Cancelado pelo produtor" (Rodada 9, item 2): quando a
cliente pede o cancelamento pelo suporte, o Luiz cancela pela Kiwify e o motivo
vem como produtor — mas a saída é real e CONTA como churn. A única exceção é
cancelamento casado com upgrade/reassinatura em ≤30 dias, que já chega marcado
com `is_plan_change=True` pelo pareamento do recorder.
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


def test_cancelado_pelo_produtor_conta_churn(db):
    """Rodada 9, item 2 (caso Bruna Cabral): cancelamento via produtor é saída real."""
    # Ativa desde antes de julho, pra entrar no denominador de "início do mês".
    _evento(db, event_type="order_approved", customer_cpf="ajuste",
            received_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            has_access=True, access_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
    _evento(db, event_type="subscription_canceled", customer_cpf="ajuste",
            received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            cancel_reason="Cancelado pelo produtor", has_access=False, access_until=None)

    churn = AdminMetricsService(db).churn_for_month(2026, 7)
    assert churn["count"] == 1


def test_produtor_casado_com_upgrade_nao_conta(db):
    """A exceção continua sendo o upgrade: o pareamento marca is_plan_change=True
    (casos Ana Ariel e Luiz Fernando) e esse cancelamento fica fora do churn."""
    _evento(db, event_type="order_approved", customer_cpf="upgrade",
            received_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
            has_access=True, access_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
    _evento(db, event_type="subscription_canceled", customer_cpf="upgrade",
            received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
            cancel_reason="Cancelado pelo produtor", is_plan_change=True,
            has_access=False, access_until=None)

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


def test_mistura_produtor_conta_upgrade_nao(db):
    """Agosto real da Rodada 9: 3 cancelamentos por produtor — 2 casados com
    upgrade (fora) e 1 saída de verdade (dentro), mais 1 por falta de pagamento."""
    for cpf, motivo, plan_change in [
        ("ana", "Cancelado pelo produtor", True),      # upgrade → fora
        ("luizf", "Cancelado pelo produtor", True),    # upgrade → fora
        ("bruna", "Cancelado pelo produtor", False),   # saiu de verdade → conta
        ("real1", "Pagamento não efetuado", False),    # churn normal → conta
    ]:
        _evento(db, event_type="order_approved", customer_cpf=cpf,
                received_at=datetime(2026, 6, 1, tzinfo=timezone.utc),
                has_access=True, access_until=datetime(2026, 12, 31, tzinfo=timezone.utc))
        _evento(db, event_type="subscription_canceled", customer_cpf=cpf,
                received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
                cancel_reason=motivo, is_plan_change=plan_change,
                has_access=False, access_until=None)

    churn = AdminMetricsService(db).churn_for_month(2026, 7)
    assert churn["count"] == 2
