"""Regressão: dedupe de cobrança duplicada (item 1) e prioridade de evento pro
estado atual da assinatura (item 3 — next_payment não pode vir de webhook velho).
Task 2: status atrasado, late ≠ churn, late ≠ revenue.
Task 3: série MRR só com meses de histórico real."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import (
    CANCEL_EVENTS,
    PAID_EVENTS,
    AdminMetricsService,
    _client_display_status,
    _dedupe_by_charge,
    _is_active_now,
    _latest_by_subscriber,
    _normalize_plan_label,
    _paid_total_for_events,
    revenue_from_charges_for_month,
)


def _ev(**kwargs):
    defaults = dict(
        event_type="order_approved",
        order_id=None,
        subscription_id="sub-1",
        customer_email="user@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
        next_payment=None,
        subscription_status=None,
        has_access=None,
        access_until=None,
        amount_net_cents=None,
        charges_completed=None,
        card_rejection_reason=None,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_normalize_plan_keeps_max_distinct():
    assert _normalize_plan_label("Max", None) == "max"
    assert _normalize_plan_label("Pro", None) == "pro"
    assert _normalize_plan_label("MarketDash Max", "max") == "max"


def test_dedupe_by_charge_collapses_same_order_id():
    a = _ev(event_type="order_approved", order_id="c4456ec2")
    b = _ev(event_type="subscription_renewed", order_id="c4456ec2")
    result = _dedupe_by_charge([a, b])
    assert result == [a]  # primeiro visto vence, segundo é descartado


def test_dedupe_by_charge_keeps_distinct_orders():
    a = _ev(order_id="order-1")
    b = _ev(order_id="order-2")
    result = _dedupe_by_charge([a, b])
    assert result == [a, b]


def test_dedupe_by_charge_never_collapses_events_without_order_id():
    a = _ev(order_id=None)
    b = _ev(order_id=None)
    result = _dedupe_by_charge([a, b])
    assert result == [a, b]


def test_latest_by_subscriber_prefers_subscription_renewed_over_later_order_approved():
    """Caso real (Letícia, 25/07/2026): order_approved chegou ~83ms DEPOIS de
    subscription_renewed pro mesmo subscription_id, mas com next_payment
    desatualizado (do dia da renovação, não do próximo ciclo). O evento mais
    'recente' por received_at não pode vencer aqui — subscription_renewed é
    quem sabe o next_payment certo."""
    now = datetime(2026, 7, 25, 6, 15, 55, tzinfo=timezone.utc)
    renewed = _ev(
        event_type="subscription_renewed",
        next_payment=datetime(2026, 8, 25, tzinfo=timezone.utc),  # correto
        received_at=now,
    )
    approved = _ev(
        event_type="order_approved",
        next_payment=datetime(2026, 7, 25, 8, 55, tzinfo=timezone.utc),  # desatualizado
        received_at=now + timedelta(milliseconds=83),  # chegou DEPOIS
    )
    latest = _latest_by_subscriber([renewed, approved])
    assert len(latest) == 1
    chosen = next(iter(latest.values()))
    assert chosen is renewed
    assert chosen.next_payment == datetime(2026, 8, 25, tzinfo=timezone.utc)


def test_latest_by_subscriber_still_advances_on_genuinely_newer_renewal():
    """Garantir que a prioridade não trava o estado no primeiro subscription_renewed
    pra sempre — uma renovação seguinte (mais tarde) deve vencer normalmente."""
    first_renewal = _ev(
        event_type="subscription_renewed",
        next_payment=datetime(2026, 8, 25, tzinfo=timezone.utc),
        received_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    second_renewal = _ev(
        event_type="subscription_renewed",
        next_payment=datetime(2026, 9, 25, tzinfo=timezone.utc),
        received_at=datetime(2026, 8, 25, tzinfo=timezone.utc),
    )
    latest = _latest_by_subscriber([first_renewal, second_renewal])
    chosen = next(iter(latest.values()))
    assert chosen is second_renewal


def test_latest_by_subscriber_cancellation_after_renewal_wins():
    renewal = _ev(
        event_type="subscription_renewed",
        received_at=datetime(2026, 7, 25, tzinfo=timezone.utc),
    )
    cancel = _ev(
        event_type="subscription_canceled",
        received_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )
    latest = _latest_by_subscriber([renewal, cancel])
    chosen = next(iter(latest.values()))
    assert chosen is cancel


def test_latest_by_subscriber_late_beats_later_order_approved():
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    late = _ev(event_type="subscription_late", received_at=now)
    approved = _ev(
        event_type="order_approved",
        received_at=now + timedelta(milliseconds=50),
    )
    latest = _latest_by_subscriber([late, approved])
    assert next(iter(latest.values())) is late


def test_latest_by_subscriber_later_renew_clears_late():
    """Late e renew no mesmo tier: received_at decide — renew posterior limpa atrasado."""
    now = datetime(2026, 7, 25, tzinfo=timezone.utc)
    late = _ev(
        event_type="subscription_late",
        subscription_status="waiting_payment",
        has_access=True,
        access_until=datetime(2026, 8, 20, tzinfo=timezone.utc),
        received_at=now,
    )
    renew = _ev(
        event_type="subscription_renewed",
        subscription_status="active",
        has_access=True,
        access_until=datetime(2026, 8, 25, tzinfo=timezone.utc),
        received_at=now + timedelta(hours=2),
    )
    latest = _latest_by_subscriber([late, renew])
    chosen = next(iter(latest.values()))
    assert chosen is renew
    assert chosen.event_type == "subscription_renewed"
    is_active = _is_active_now(chosen, date(2026, 7, 28))
    assert _client_display_status(chosen, is_active=is_active) == "ativo"
    assert _client_display_status(chosen, is_active=is_active) != "atrasado"


def test_late_with_expired_access_is_atrasado_not_active():
    today = date(2026, 7, 28)
    ev = _ev(
        event_type="subscription_late",
        subscription_status="waiting_payment",
        has_access=True,
        access_until=datetime(2026, 7, 20, tzinfo=timezone.utc),
        card_rejection_reason="refused_bank",
    )
    assert _is_active_now(ev, today) is False
    assert _client_display_status(ev, is_active=False) == "atrasado"


def test_late_with_valid_access_is_atrasado_still_active_eligible():
    today = date(2026, 7, 28)
    ev = _ev(
        event_type="subscription_late",
        subscription_status="waiting_payment",
        has_access=True,
        access_until=datetime(2026, 8, 20, tzinfo=timezone.utc),
    )
    assert _is_active_now(ev, today) is True
    assert _client_display_status(ev, is_active=True) == "atrasado"


def test_waiting_payment_status_alone_is_atrasado():
    ev = _ev(event_type="order_approved", subscription_status="waiting_payment")
    assert _client_display_status(ev, is_active=False) == "atrasado"


def test_late_does_not_count_as_churn():
    assert CANCEL_EVENTS == {"subscription_canceled"}
    assert "subscription_late" not in CANCEL_EVENTS
    late = _ev(event_type="subscription_late", subscription_status="waiting_payment")
    assert _client_display_status(late, is_active=False) == "atrasado"
    assert _client_display_status(late, is_active=False) != "inativo"


def test_canceled_without_access_is_inativo_churn():
    ev = _ev(event_type="subscription_canceled", subscription_status="canceled")
    assert _client_display_status(ev, is_active=False) == "inativo"


def test_canceled_with_access_is_cancelado_com_acesso():
    ev = _ev(event_type="subscription_canceled", subscription_status="canceled")
    assert _client_display_status(ev, is_active=True) == "cancelado_com_acesso"


def test_subscription_late_not_in_revenue():
    """Late sem cobrança paid não altera faturamento."""
    late = _ev(
        event_type="subscription_late",
        order_id="late-1",
        amount_net_cents=9999,
        charges_completed=[
            {
                "order_id": "w1",
                "status": "waiting_payment",
                "Commissions": {"my_commission": 9999, "charge_amount": 10000},
            }
        ],
    )
    assert "subscription_late" not in PAID_EVENTS
    assert _paid_total_for_events([late]) == 0
    assert revenue_from_charges_for_month([late], 2026, 7)["net"] == 0


def test_mrr_series_starts_at_first_event_month(monkeypatch):
    """MRR começa no mês do primeiro received_at e inclui o mês corrente."""
    fixed_now = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("app.services.admin_metrics_service.datetime", _FixedDateTime)

    db = MagicMock()
    min_q = MagicMock()
    min_q.scalar.return_value = datetime(2026, 7, 20, tzinfo=timezone.utc)
    all_q = MagicMock()
    all_q.all.return_value = []
    db.query.side_effect = [min_q, all_q]

    svc = AdminMetricsService(db)
    monkeypatch.setattr(
        svc, "revenue_for_month", lambda y, m: {"net": 100, "gross": 110, "refund_net": 0}
    )
    monkeypatch.setattr(svc, "active_subscribers", lambda as_of=None: [])
    monkeypatch.setattr(svc, "mrr_cents", lambda actives=None: {"net": 50, "gross": 55})

    series = svc.series_12m()
    mrr_months = [p["month"] for p in series["mrr"]]
    rev_months = [p["month"] for p in series["revenue"]]

    assert mrr_months == ["2026-07", "2026-08", "2026-09"]
    assert all(m >= "2026-07" for m in mrr_months)
    assert "2026-06" not in mrr_months
    assert rev_months == ["2026-07", "2026-08", "2026-09"]


def test_mrr_series_empty_when_no_events(monkeypatch):
    db = MagicMock()
    min_q = MagicMock()
    min_q.scalar.return_value = None
    db.query.return_value = min_q

    series = AdminMetricsService(db).series_12m()
    assert series == {"mrr": [], "revenue": []}


def test_mrr_series_includes_current_month_when_first_event_is_now(monkeypatch):
    """Primeiro evento no mês corrente → MRR inclui ponto parcial de julho."""
    fixed_now = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("app.services.admin_metrics_service.datetime", _FixedDateTime)

    db = MagicMock()
    min_q = MagicMock()
    min_q.scalar.return_value = datetime(2026, 7, 20, tzinfo=timezone.utc)
    all_q = MagicMock()
    all_q.all.return_value = []
    db.query.side_effect = [min_q, all_q]

    svc = AdminMetricsService(db)
    monkeypatch.setattr(
        svc, "revenue_for_month", lambda y, m: {"net": 100, "gross": 110, "refund_net": 0}
    )
    # Rodada 4: a série usa mrr_at (reconstrução por cobrança), não mais
    # active_subscribers/mrr_cents.
    monkeypatch.setattr(
        svc, "mrr_at", lambda momento, periodos=None: {"net": 50, "gross": 55}
    )

    series = svc.series_12m()
    assert series["mrr"][0]["month"] == "2026-07"
    assert series["mrr"][0]["net"] > 0
    assert len(series["mrr"]) == 1
    assert [p["month"] for p in series["revenue"]] == ["2026-07"]


def test_revenue_series_starts_at_earliest_charge_month(monkeypatch):
    """Backfill com paid_at anterior ao primeiro received_at estende a série."""
    fixed_now = datetime(2026, 7, 28, 12, 0, 0, tzinfo=timezone.utc)

    class _FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_now

    monkeypatch.setattr("app.services.admin_metrics_service.datetime", _FixedDateTime)

    ev = _ev(
        received_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        charges_completed=[
            {
                "order_id": "backfill-1",
                "status": "paid",
                "approved_date": "2026-04-28T12:00:00Z",
                "Commissions": {"my_commission": 10000, "charge_amount": 11000},
            }
        ],
    )

    db = MagicMock()
    min_q = MagicMock()
    min_q.scalar.return_value = datetime(2026, 7, 20, tzinfo=timezone.utc)
    all_q = MagicMock()
    all_q.all.return_value = [ev]
    db.query.side_effect = [min_q, all_q]

    svc = AdminMetricsService(db)
    monkeypatch.setattr(
        svc, "revenue_for_month", lambda y, m: {"net": 100, "gross": 110, "refund_net": 0}
    )
    monkeypatch.setattr(svc, "active_subscribers", lambda as_of=None: [])
    monkeypatch.setattr(svc, "mrr_cents", lambda actives=None: {"net": 50, "gross": 55})

    series = svc.series_12m()
    rev_months = [p["month"] for p in series["revenue"]]
    mrr_months = [p["month"] for p in series["mrr"]]
    assert "2026-04" in rev_months
    assert "2026-07" in rev_months
    assert rev_months == ["2026-04", "2026-05", "2026-06", "2026-07"]
    # Rodada 4: o MRR passou a ser RECONSTRUÍDO das cobranças, então a série
    # começa no mês da cobrança retroativa junto com o faturamento — antes ela
    # começava no primeiro received_at e o passado ficava sem MRR.
    assert mrr_months == ["2026-04", "2026-05", "2026-06", "2026-07"]


def test_mrr_cents_nao_perde_centavos_com_divisao_por_assinante():
    """3 assinantes trimestrais com resto não-nulo cada (100/3 = 33,33...) — a
    divisão inteira por assinante (100 // 3 = 33) perderia 1 centavo em cada um,
    3 no total. Precisão cheia + arredondar só a soma preserva o valor exato."""
    from app.services.admin_metrics_service import AdminMetricsService

    svc = AdminMetricsService.__new__(AdminMetricsService)  # sem DB
    svc._last_paid_for = lambda ev: None  # força usar amount_net_cents/gross direto
    actives = [
        SimpleNamespace(plan_frequency="quarterly", amount_net_cents=100, amount_gross_cents=100)
        for _ in range(3)
    ]
    result = svc.mrr_cents(actives)
    assert result["net"] == 100  # não 99 (3 × (100 // 3))
    assert result["gross"] == 100
