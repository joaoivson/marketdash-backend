"""Denominador do churn — a base do INÍCIO do mês.

Rodada 7, item 2: cancelado-com-acesso não é receita recorrente esperada e não
pode inflar o denominador.

Rodada 8, item 1: o denominador deixou de ser derivado do estado ATUAL de cada
assinante (`renewing_subscribers(as_of=...)`, que olha o último evento de todos
— inclusive os recebidos DEPOIS do corte) e passou a ser o retrato reconstruído
das cobranças no instante anterior ao início do mês. Sem isso, quem assinou em
agosto entrava no denominador de agosto e quem cancelou em agosto saía dele —
justamente quem o churn quer medir. Produção mostrava 6/41.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        subscription_id="sub-1",
        customer_email="a@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        approved_date=datetime(2026, 7, 10, tzinfo=timezone.utc),
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=None,
        next_payment=None,
        amount_net_cents=6000,
        amount_gross_cents=6700,
        fee_cents=None,
        refunded_at=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _svc(eventos, cancelamentos_do_mes=None):
    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: eventos
    svc.db.query.return_value.filter.return_value.all.return_value = (
        cancelamentos_do_mes or []
    )
    svc._agora = lambda: datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)
    return svc


def test_cancelado_com_acesso_nao_infla_o_denominador():
    """20 pagando + 17 que já tinham cancelado antes do corte = base 20, não 37."""
    renovando = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    canceladas_antes = []
    for i in range(17):
        chave = f"sub-canc-{i}"
        canceladas_antes.append(
            _ev(id=100 + i, subscription_id=chave, customer_email=f"c{i}@ex.com")
        )
        canceladas_antes.append(
            _ev(
                id=200 + i,
                subscription_id=chave,
                customer_email=f"c{i}@ex.com",
                event_type="subscription_canceled",
                subscription_status="canceled",
                received_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
                canceled_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
            )
        )

    resultado = _svc(renovando + canceladas_antes).churn_for_month(2026, 8)

    assert resultado["start_actives"] == 20


def test_quem_assinou_depois_do_corte_nao_entra_no_denominador():
    """O bug de produção: assinante de agosto contava na base de 31/07."""
    base_julho = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    entrou_em_agosto = [
        _ev(
            id=50 + i,
            subscription_id=f"sub-novo-{i}",
            customer_email=f"n{i}@ex.com",
            received_at=datetime(2026, 8, 5, tzinfo=timezone.utc),
            approved_date=datetime(2026, 8, 5, tzinfo=timezone.utc),
            access_until=datetime(2026, 9, 5, tzinfo=timezone.utc),
        )
        for i in range(9)
    ]

    resultado = _svc(base_julho + entrou_em_agosto).churn_for_month(2026, 8)

    assert resultado["start_actives"] == 20


def test_quem_cancelou_no_mes_continua_no_denominador():
    """Cancelou dia 10/08: é o churn do mês — tem que estar na base de 31/07."""
    base = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    cancelou_em_agosto = _ev(
        id=99,
        subscription_id="sub-5",
        customer_email="r5@ex.com",
        event_type="subscription_canceled",
        subscription_status="canceled",
        received_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
        canceled_at=datetime(2026, 8, 10, tzinfo=timezone.utc),
    )

    resultado = _svc(base + [cancelou_em_agosto], [cancelou_em_agosto]).churn_for_month(
        2026, 8
    )

    assert resultado["start_actives"] == 20
    assert resultado["count"] == 1
    assert resultado["rate"] == 0.05


def test_atrasada_no_corte_conta_na_base():
    """Vigência vencida mas SEM cancelamento: a assinatura existe e pode churnar.

    Caso real de produção (Rodada 8): duas assinaturas venceram em 28 e 29/07,
    ficaram em `subscription_late` e só foram canceladas em 04 e 05/08 por
    "Pagamento não efetuado". Elas apareciam no numerador do churn de agosto sem
    estar no denominador — aritmeticamente inválido.
    """
    base = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    atrasada = [
        _ev(
            id=90,
            subscription_id="sub-late",
            customer_email="late@ex.com",
            received_at=datetime(2026, 6, 29, tzinfo=timezone.utc),
            approved_date=datetime(2026, 6, 29, tzinfo=timezone.utc),
        ),
        _ev(
            id=91,
            subscription_id="sub-late",
            customer_email="late@ex.com",
            event_type="subscription_late",
            subscription_status="waiting_payment",
            received_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
            approved_date=None,
        ),
    ]

    resultado = _svc(base + atrasada).churn_for_month(2026, 8)

    assert resultado["start_actives"] == 21


def test_cancelada_antes_do_corte_sai_da_base_mesmo_marcada_como_troca_de_plano():
    """Caso real (Deivit, 31/07/2026): pagou 22:05 e cancelou 22:07, MESMO plano
    dos dois lados, mas a Kiwify marcou os 4 eventos com `is_plan_change=True`.

    `cancel_instants()` ignora cancelamento de troca de plano — de propósito, pra
    upgrade não virar churn. O efeito colateral é que essa pessoa ficava viva no
    retrato para sempre e inflava o denominador em 1. Era o mesmo flag errado que
    já tinha derrubado a contagem de `new_subscriptions()`.
    """
    base = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    pagou = _ev(
        id=90, subscription_id="sub-deivit", customer_email="deivit@ex.com",
        received_at=datetime(2026, 7, 31, 22, 5, tzinfo=timezone.utc),
        approved_date=datetime(2026, 7, 31, 22, 5, tzinfo=timezone.utc),
        is_plan_change=True,
    )
    cancelou = _ev(
        id=91, subscription_id="sub-deivit", customer_email="deivit@ex.com",
        event_type="subscription_canceled", subscription_status="canceled",
        received_at=datetime(2026, 7, 31, 22, 7, tzinfo=timezone.utc),
        canceled_at=datetime(2026, 7, 31, 22, 7, tzinfo=timezone.utc),
        cancel_reason="Cancelado pelo comprador",
        is_plan_change=True,
    )

    resultado = _svc(base + [pagou, cancelou]).churn_for_month(2026, 8)

    assert resultado["start_actives"] == 20


def test_upgrade_no_meio_do_mes_nao_tira_a_pessoa_da_base():
    """Rede de proteção do teste acima: quem troca de plano e SEGUE assinando
    continua na base — a assinatura nova cobre o corte."""
    base = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    antiga = _ev(
        id=90, subscription_id="sub-velha", customer_email="upgrade@ex.com",
        event_type="subscription_canceled", subscription_status="canceled",
        received_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        canceled_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
        is_plan_change=True,
    )
    nova = _ev(
        id=91, subscription_id="sub-nova", customer_email="upgrade@ex.com",
        received_at=datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc),
        approved_date=datetime(2026, 7, 20, 0, 1, tzinfo=timezone.utc),
        plan_name="Max", plan_id="max", amount_gross_cents=9700,
    )

    resultado = _svc(base + [antiga, nova]).churn_for_month(2026, 8)

    assert resultado["start_actives"] == 21


def test_todo_mundo_do_numerador_esta_no_denominador():
    """Invariante do churn: o numerador é subconjunto da base — exceto quem
    nasceu e morreu dentro do mês, que não pertence à base de abertura."""
    base = [
        _ev(id=i, subscription_id=f"sub-{i}", customer_email=f"r{i}@ex.com")
        for i in range(1, 21)
    ]
    cancelou = _ev(
        id=99,
        subscription_id="sub-3",
        customer_email="r3@ex.com",
        event_type="subscription_canceled",
        subscription_status="canceled",
        received_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
        canceled_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
    )

    resultado = _svc(base + [cancelou], [cancelou]).churn_for_month(2026, 8)

    assert resultado["count"] <= resultado["start_actives"]
