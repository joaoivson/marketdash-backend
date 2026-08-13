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
    monkeypatch.setattr(svc, "new_vs_canceled_series", lambda: [])

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
    assert series == {"mrr": [], "revenue": [], "new_vs_canceled": []}


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
    # active_subscribers/mrr_cents. Rodada 6 item 2: mrr_at ganhou o parâmetro
    # `cancelamentos` (série passa periodos e cancelamentos posicionalmente).
    monkeypatch.setattr(
        svc, "mrr_at", lambda momento, periodos=None, cancelamentos=None: {"net": 50, "gross": 55}
    )
    monkeypatch.setattr(svc, "new_vs_canceled_series", lambda: [])

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

    # Rodada 6 item 1: a cobrança backfillada vem do próprio evento
    # (order_ref/amount_net_cents/approved_date no topo), não mais do array
    # charges_completed — webhook recebido em julho, mas approved_date de abril
    # (cobrança de fato aprovada antes, só registrada depois).
    ev = _ev(
        received_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        order_ref="backfill-1",
        amount_net_cents=10000,
        amount_gross_cents=11000,
        approved_date=datetime(2026, 4, 28, 12, 0, tzinfo=timezone.utc),
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
    monkeypatch.setattr(svc, "new_vs_canceled_series", lambda: [])

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


def test_faturamento_do_mes_nao_dobra_com_import_e_webhook():
    """Rodada 6 item 1: mesma cobrança vinda do import e do webhook conta uma vez."""
    from app.services.admin_metrics_service import revenue_from_charges_for_month

    importado = SimpleNamespace(
        id=1,
        event_type="order_approved",
        order_id="QTqDAVh",
        order_ref="QTqDAVh",
        dedupe_key="import:cobranca:QTqDAVh",
        amount_net_cents=18150,
        amount_gross_cents=19700,
        fee_cents=1550,
        approved_date=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    webhook = SimpleNamespace(
        **{**importado.__dict__, "id": 2, "order_id": "uuid", "dedupe_key": "wh:2"}
    )
    rev = revenue_from_charges_for_month([importado, webhook], 2026, 8)
    assert rev["net"] == 18150


def test_mrr_cents_nao_perde_centavos_com_divisao_por_assinante():
    """3 assinantes trimestrais com resto não-nulo cada (100/3 = 33,33...) — a
    divisão inteira por assinante (100 // 3 = 33) perderia 1 centavo em cada um,
    3 no total. Precisão cheia + arredondar só a soma preserva o valor exato.

    Rodada 7, item 3: `gross` passou a vir do preço de TABELA do plano/
    frequência (não mais de `amount_gross_cents`), por isso os eventos
    precisam de `plan_name`/`plan_id` — aqui "essencial" trimestral, cujo
    preço de tabela (11700) é exato ao dividir por 3, então a checagem de
    precisão do bruto passa a ser coberta pelo teste de
    `test_bruto_usa_preco_de_tabela_nao_ultima_cobranca` (14700 / 3 = 4900).
    `net` continua vindo de `amount_net_cents` e segue testando a precisão."""
    from app.services.admin_metrics_service import AdminMetricsService

    svc = AdminMetricsService.__new__(AdminMetricsService)  # sem DB
    svc._last_paid_for = lambda ev: None  # força usar amount_net_cents/gross direto
    actives = [
        SimpleNamespace(
            plan_frequency="quarterly",
            plan_name="Essencial",
            plan_id="essencial",
            amount_net_cents=100,
            amount_gross_cents=100,
        )
        for _ in range(3)
    ]
    result = svc.mrr_cents(actives)
    assert result["net"] == 100  # não 99 (3 × (100 // 3))
    # Bruto = preço de tabela essencial/trimestral (11700), não amount_gross_cents.
    assert result["gross"] == 11700


def test_serie_novas_x_canceladas_cobre_12_meses_ate_o_atual():
    """Rodada 6 item 11: barras pareadas por mês, últimos 12 meses."""
    from datetime import datetime as dt

    svc = AdminMetricsService(MagicMock())
    svc._agora = lambda: dt(2026, 8, 11, tzinfo=timezone.utc)
    svc.new_subscriptions = lambda y, m: 14 if (y, m) == (2026, 8) else 0
    svc.churn_for_month = lambda y, m: {
        "count": 4 if (y, m) == (2026, 8) else 0,
        "rate": 0.0,
        "start_actives": 0,
    }

    serie = svc.new_vs_canceled_series()
    assert len(serie) == 12
    assert serie[-1] == {"month": "2026-08", "novas": 14, "canceladas": 4}
    assert serie[0]["month"] == "2025-09"


def _mock_db_for_list_clients():
    """DB genérico pra list_clients: toda query encadeada (filter/order_by)
    retorna vazio/None — só os eventos passados via `_all_events` monkeypatch
    importam pro teste."""
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        q.filter.return_value = q
        q.filter_by.return_value = q
        q.order_by.return_value = q
        q.first.return_value = None
        q.all.return_value = []
        q.scalar.return_value = None
        return q

    db.query.side_effect = query_side_effect
    return db


def test_list_clients_colapsa_upgrade_de_plano_numa_unica_linha():
    """Task 3b: upgrade de plano gera um subscription_id NOVO na Kiwify — o
    mesmo CPF aparece com duas subscriber_keys (plano antigo cancelado +
    plano novo ativo). list_clients() deve mostrar só a linha ATUAL (Pro,
    ativa), não a superada (Essencial, cancelada por troca de plano)."""
    cpf = "111.111.111-11"
    t1 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 5, tzinfo=timezone.utc)

    superseded = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-old-1",
        customer_cpf=cpf,
        customer_email="cliente.ficticio@example.com",
        customer_name="Cliente Ficticio",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=True,
        received_at=t1,
    )
    current = _ev(
        event_type="order_approved",
        subscription_id="sub-new-1",
        customer_cpf=cpf,
        customer_email="cliente.ficticio@example.com",
        customer_name="Cliente Ficticio",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="active",
        has_access=True,
        is_plan_change=True,
        received_at=t2,
    )

    svc = AdminMetricsService(_mock_db_for_list_clients())
    svc._all_events = lambda: [superseded, current]
    svc._semaphore = lambda uid: "red"

    rows = svc.list_clients({})
    cpf_rows = [r for r in rows if r["cpf"] == cpf]

    assert len(cpf_rows) == 1
    row = cpf_rows[0]
    assert row["plan"] == "pro"
    assert row["status"] == "ativo"
    assert row["subscription_id"] == "sub-new-1"


def test_list_clients_nao_colapsa_cancelamento_genuino_nao_ligado_a_troca():
    """Regressão: um CPF com histórico de cancelamento REAL (is_plan_change=False)
    seguido de nova assinatura independente meses depois continua mostrando
    as DUAS linhas — esse caso não pode ser colapsado pela regra de upgrade."""
    cpf = "222.222.222-22"
    t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 5, tzinfo=timezone.utc)

    churn_antigo = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-antigo-1",
        customer_cpf=cpf,
        customer_email="outra.ficticia@example.com",
        customer_name="Outra Ficticia",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=False,
        received_at=t1,
    )
    reassinatura_nova = _ev(
        event_type="order_approved",
        subscription_id="sub-nova-2",
        customer_cpf=cpf,
        customer_email="outra.ficticia@example.com",
        customer_name="Outra Ficticia",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="active",
        has_access=True,
        is_plan_change=False,
        received_at=t2,
    )

    svc = AdminMetricsService(_mock_db_for_list_clients())
    svc._all_events = lambda: [churn_antigo, reassinatura_nova]
    svc._semaphore = lambda uid: "red"

    rows = svc.list_clients({})
    cpf_rows = [r for r in rows if r["cpf"] == cpf]

    assert len(cpf_rows) == 2
    statuses = {r["status"] for r in cpf_rows}
    assert statuses == {"inativo", "ativo"}


def _mock_db_for_list_clients_com_cobrancas(por_subscription_id=None, por_cpf=None):
    """Variante de `_mock_db_for_list_clients` que sabe responder à query de
    total pago (`sub_filter`) de acordo com a coluna/valor usados no filtro —
    necessário pra testar que o total pago do CPF colapsado soma as cobranças
    das DUAS subscription_id (Finding 2), não só da que sobreviveu."""
    por_subscription_id = por_subscription_id or {}
    por_cpf = por_cpf or {}
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()

        def filter_side_effect(*conditions):
            resultado = MagicMock()
            resultado.filter.return_value = resultado
            resultado.filter_by.return_value = resultado
            resultado.order_by.return_value = resultado
            resultado.first.return_value = None
            resultado.scalar.return_value = None
            resultado.all.return_value = []
            if conditions:
                condicao = conditions[0]
                try:
                    coluna = condicao.left.name
                    valor = condicao.right.value
                except AttributeError:
                    coluna, valor = None, None
                if coluna == "subscription_id" and isinstance(valor, list):
                    # `.in_([...])` — o fix do merge escopado (task 3b) usa isso
                    # pra somar só os subscription_id absorvidos por ESTE
                    # sobrevivente, não o CPF inteiro.
                    combinados = []
                    vistos = set()
                    for sid in valor:
                        for ev in por_subscription_id.get(sid, []):
                            if id(ev) not in vistos:
                                vistos.add(id(ev))
                                combinados.append(ev)
                    resultado.all.return_value = combinados
                elif coluna == "subscription_id" and valor in por_subscription_id:
                    resultado.all.return_value = por_subscription_id[valor]
                elif coluna == "customer_cpf" and valor in por_cpf:
                    resultado.all.return_value = por_cpf[valor]
            return resultado

        q.filter.side_effect = filter_side_effect
        q.filter_by.return_value = q
        q.order_by.return_value = q
        q.first.return_value = None
        q.all.return_value = []
        q.scalar.return_value = None
        return q

    db.query.side_effect = query_side_effect
    return db


def test_list_clients_upgrade_seguido_de_churn_genuino_nao_apaga_o_cliente():
    """Finding 1 (Critical, task-3b): upgrade Essencial→Pro no dia 1 marca a
    Essencial cancelada como is_plan_change=True (correto). Se a cliente
    GENUINAMENTE cancelar a Pro no dia 10, encontrar_par_de_plan_change (fora
    do escopo desse fix) pareia esse cancelamento contra o pagamento Essencial
    original (dentro de ±30 dias, plano diferente) e também marca
    is_plan_change=True — mesmo não sendo uma troca de plano de verdade. As
    DUAS subscriber_keys do CPF batem a condição de remoção do de-dup, e o
    código antigo (sem guarda) apagava as duas: a cliente sumia da lista
    inteira. O fix garante que pelo menos uma linha sobra — a mais recente."""
    cpf = "333.333.333-33"
    t1 = datetime(2026, 6, 1, tzinfo=timezone.utc)  # upgrade Essencial -> Pro
    t2 = datetime(2026, 6, 10, tzinfo=timezone.utc)  # churn genuíno da Pro

    upgrade_superado = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-old-1",
        customer_cpf=cpf,
        customer_email="upgrade.depois.churn@example.com",
        customer_name="Upgrade Depois Churn",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=True,
        received_at=t1,
    )
    churn_genuino_mal_marcado = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-new-1",
        customer_cpf=cpf,
        customer_email="upgrade.depois.churn@example.com",
        customer_name="Upgrade Depois Churn",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=True,  # pareado (erroneamente) contra o pagamento Essencial
        received_at=t2,
    )

    svc = AdminMetricsService(_mock_db_for_list_clients())
    svc._all_events = lambda: [upgrade_superado, churn_genuino_mal_marcado]
    svc._semaphore = lambda uid: "red"

    rows = svc.list_clients({})
    cpf_rows = [r for r in rows if r["cpf"] == cpf]

    assert len(cpf_rows) == 1  # nunca zero — a cliente não pode sumir
    assert cpf_rows[0]["subscription_id"] == "sub-new-1"  # a mais recente por received_at


def test_list_clients_total_pago_soma_as_duas_assinaturas_no_upgrade():
    """Finding 2 (Important, task-3b): quando o de-dup colapsa um CPF em uma
    única linha (upgrade Essencial→Pro), o total pago exibido tinha passado a
    considerar só a subscription_id que sobreviveu (a nova) — o que a cliente
    pagou sob a assinatura antiga (superada) sumia do total, do CSV e da
    ficha. O total exibido tem que somar as cobranças das DUAS
    subscription_id desse CPF."""
    cpf = "444.444.444-44"
    t1 = datetime(2026, 5, 1, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 5, tzinfo=timezone.utc)

    superado = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-old-2",
        customer_cpf=cpf,
        customer_email="soma.total.upgrade@example.com",
        customer_name="Soma Total Upgrade",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=True,
        received_at=t1,
    )
    atual = _ev(
        event_type="order_approved",
        subscription_id="sub-new-2",
        customer_cpf=cpf,
        customer_email="soma.total.upgrade@example.com",
        customer_name="Soma Total Upgrade",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="active",
        has_access=True,
        is_plan_change=True,
        received_at=t2,
    )

    cobranca_antiga = _ev(
        event_type="order_approved",
        order_id="order-old-2",
        subscription_id="sub-old-2",
        customer_cpf=cpf,
        amount_net_cents=5000,
        received_at=t1,
    )
    cobranca_nova = _ev(
        event_type="order_approved",
        order_id="order-new-2",
        subscription_id="sub-new-2",
        customer_cpf=cpf,
        amount_net_cents=3000,
        received_at=t2,
    )

    db = _mock_db_for_list_clients_com_cobrancas(
        por_subscription_id={
            "sub-old-2": [cobranca_antiga],
            "sub-new-2": [cobranca_nova],
        },
        por_cpf={
            cpf: [cobranca_antiga, cobranca_nova],
        },
    )

    svc = AdminMetricsService(db)
    svc._all_events = lambda: [superado, atual]
    svc._semaphore = lambda uid: "red"

    rows = svc.list_clients({})
    cpf_rows = [r for r in rows if r["cpf"] == cpf]

    assert len(cpf_rows) == 1
    # 5000 (assinatura antiga) + 3000 (assinatura nova) — não só os 3000 da sobrevivente
    assert cpf_rows[0]["total_paid_net_cents"] == 8000


def test_list_clients_merge_de_upgrade_nao_contamina_assinatura_independente_do_mesmo_cpf():
    """Finding (task 3b, reproduzido pelo revisor): `collapsed_cpfs` sabia
    QUAL CPF teve merge, mas não QUAL sobrevivente absorveu qual grupo
    apagado. Com 3 grupos no mesmo CPF — uma assinatura antiga e
    genuinamente independente (`sub-antiga`, is_plan_change=False, já
    encerrada em 2024) e um par de upgrade em 2026 (`sub-old` superada,
    `sub-new` sobrevivente) — o filtro largo por `customer_cpf ==` aplicava
    a soma combinada às DUAS linhas sobreviventes, inflando o total da
    assinatura antiga com dinheiro de uma assinatura que ela nunca teve.
    O fix escopa a soma só ao par que de fato se fundiu."""
    cpf = "555.555.555-55"
    t_antiga = datetime(2024, 1, 1, tzinfo=timezone.utc)
    t_old = datetime(2026, 6, 1, tzinfo=timezone.utc)
    t_new = datetime(2026, 6, 5, tzinfo=timezone.utc)

    antiga_churn = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-antiga",
        customer_cpf=cpf,
        customer_email="tres.grupos@example.com",
        customer_name="Tres Grupos",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=False,
        received_at=t_antiga,
    )
    old_superado = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-old",
        customer_cpf=cpf,
        customer_email="tres.grupos@example.com",
        customer_name="Tres Grupos",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=True,
        received_at=t_old,
    )
    novo_atual = _ev(
        event_type="order_approved",
        subscription_id="sub-new",
        customer_cpf=cpf,
        customer_email="tres.grupos@example.com",
        customer_name="Tres Grupos",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="active",
        has_access=True,
        is_plan_change=True,
        received_at=t_new,
    )

    cobranca_antiga = _ev(
        event_type="order_approved",
        order_id="order-antiga",
        subscription_id="sub-antiga",
        customer_cpf=cpf,
        amount_net_cents=9000,
        received_at=t_antiga,
    )
    cobranca_old = _ev(
        event_type="order_approved",
        order_id="order-old",
        subscription_id="sub-old",
        customer_cpf=cpf,
        amount_net_cents=5000,
        received_at=t_old,
    )
    cobranca_new = _ev(
        event_type="order_approved",
        order_id="order-new",
        subscription_id="sub-new",
        customer_cpf=cpf,
        amount_net_cents=3000,
        received_at=t_new,
    )

    db = _mock_db_for_list_clients_com_cobrancas(
        por_subscription_id={
            "sub-antiga": [cobranca_antiga],
            "sub-old": [cobranca_old],
            "sub-new": [cobranca_new],
        },
        por_cpf={
            cpf: [cobranca_antiga, cobranca_old, cobranca_new],
        },
    )

    svc = AdminMetricsService(db)
    svc._all_events = lambda: [antiga_churn, old_superado, novo_atual]
    svc._semaphore = lambda uid: "red"

    rows = svc.list_clients({})
    cpf_rows = {r["subscription_id"]: r for r in rows if r["cpf"] == cpf}

    # sub-old foi absorvida pelo upgrade — só sub-antiga e sub-new sobram.
    assert set(cpf_rows.keys()) == {"sub-antiga", "sub-new"}

    # sub-new soma só o que foi fundido nela: sub-old (5000) + sub-new (3000).
    assert cpf_rows["sub-new"]["total_paid_net_cents"] == 8000

    # sub-antiga é uma assinatura independente — não pode ganhar o dinheiro
    # do upgrade de outro grupo só por compartilhar o CPF.
    assert cpf_rows["sub-antiga"]["total_paid_net_cents"] == 9000


def test_list_clients_candidatura_a_sobrevivente_usa_sinal_duravel_nao_evento_mais_recente():
    """Finding (Important, task 3b, reproduzido pelo revisor): a candidatura a
    sobrevivente do merge lia `is_plan_change` do evento mais recente do grupo
    (`all_latest[key]`) — um sinal EFÊMERO. Logo após o upgrade, o evento mais
    recente da sobrevivente É o próprio pagamento do upgrade (is_plan_change=
    True), então ela qualifica. Mas assim que a sobrevivente tem uma renovação
    NORMAL (subscription_renewed comum, is_plan_change=False — não é ela
    própria lado de nenhuma troca de plano), essa renovação vira o novo
    "evento mais recente" da chave (prioridade 2 > 1 de order_approved,
    `_SUBSCRIBER_STATE_PRIORITY`), e a checagem de candidatura passa a ler
    is_plan_change=False. A sobrevivente deixa de qualificar como candidata,
    o grupo antigo apagado fica sem absorvedor e o dinheiro dele (e a própria
    linha) somem do painel. O fix usa um sinal DURÁVEL: a chave tem, no
    histórico INTEIRO de eventos, algum evento com is_plan_change=True?"""
    cpf = "666.666.666-66"
    t1 = datetime(2026, 6, 1, tzinfo=timezone.utc)  # upgrade Essencial -> Pro
    t2 = datetime(2026, 6, 5, tzinfo=timezone.utc)  # pagamento inicial da Pro (upgrade)
    t3 = datetime(2026, 7, 5, tzinfo=timezone.utc)  # renovação normal da Pro, um mês depois

    old_superado = _ev(
        event_type="subscription_canceled",
        subscription_id="sub-old-3",
        customer_cpf=cpf,
        customer_email="renovacao.depois.upgrade@example.com",
        customer_name="Renovacao Depois Upgrade",
        user_id=None,
        customer_phone=None,
        plan_name="Essencial",
        plan_id="essencial",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="canceled",
        has_access=False,
        is_plan_change=True,
        received_at=t1,
    )
    novo_upgrade = _ev(
        event_type="order_approved",
        subscription_id="sub-new-3",
        customer_cpf=cpf,
        customer_email="renovacao.depois.upgrade@example.com",
        customer_name="Renovacao Depois Upgrade",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="active",
        has_access=True,
        is_plan_change=True,
        received_at=t2,
    )
    renovacao_normal = _ev(
        event_type="subscription_renewed",
        subscription_id="sub-new-3",
        customer_cpf=cpf,
        customer_email="renovacao.depois.upgrade@example.com",
        customer_name="Renovacao Depois Upgrade",
        user_id=None,
        customer_phone=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        subscription_start=None,
        subscription_status="active",
        has_access=True,
        is_plan_change=False,  # renovação comum — não é ela própria lado de troca
        received_at=t3,
    )

    cobranca_antiga = _ev(
        event_type="order_approved",
        order_id="order-antiga-3",
        subscription_id="sub-old-3",
        customer_cpf=cpf,
        amount_net_cents=5000,
        received_at=t1,
    )
    cobranca_upgrade = _ev(
        event_type="order_approved",
        order_id="order-upgrade-3",
        subscription_id="sub-new-3",
        customer_cpf=cpf,
        amount_net_cents=2000,
        received_at=t2,
    )
    cobranca_renovacao = _ev(
        event_type="subscription_renewed",
        order_id="order-renovacao-3",
        subscription_id="sub-new-3",
        customer_cpf=cpf,
        amount_net_cents=3000,
        received_at=t3,
    )

    db = _mock_db_for_list_clients_com_cobrancas(
        por_subscription_id={
            "sub-old-3": [cobranca_antiga],
            "sub-new-3": [cobranca_upgrade, cobranca_renovacao],
        },
        por_cpf={
            cpf: [cobranca_antiga, cobranca_upgrade, cobranca_renovacao],
        },
    )

    svc = AdminMetricsService(db)
    svc._all_events = lambda: [old_superado, novo_upgrade, renovacao_normal]
    svc._semaphore = lambda uid: "red"

    rows = svc.list_clients({})
    cpf_rows = [r for r in rows if r["cpf"] == cpf]

    # A renovação normal vira o "latest" da sobrevivente, mas a chave nunca
    # deixa de ter sido lado de upgrade no histórico — a linha antiga
    # (sub-old-3) não pode sumir sem deixar rastro.
    assert len(cpf_rows) == 1
    assert cpf_rows[0]["subscription_id"] == "sub-new-3"
    # 5000 (assinatura antiga) + 2000 (pagamento do upgrade) + 3000 (renovação
    # normal posterior) — as três cobranças, não só as da subscription_id
    # sobrevivente.
    assert cpf_rows[0]["total_paid_net_cents"] == 10000


def test_status_filter_aceita_lista_e_busca_ignora_filtro():
    """Rodada 6 item 10: padrão sem Inativo, mas buscar "Débora" acha a inativa."""
    from app.services.admin_metrics_service import _status_permitido

    padrao = "ativo,atrasado,cancelado_com_acesso"
    assert _status_permitido("ativo", padrao, busca=None) is True
    assert _status_permitido("inativo", padrao, busca=None) is False
    # com busca ativa, o filtro de status não elimina ninguém
    assert _status_permitido("inativo", padrao, busca="debora") is True
    # sem filtro, tudo passa
    assert _status_permitido("inativo", None, busca=None) is True
