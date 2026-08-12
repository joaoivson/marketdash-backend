"""Rodada 6, item 4: quem cancela no vencimento conta como NÃO-renovação.

Agosto/2026 até 11/08: Alexandre renovou, Girlene venceu 10/08 e cancelou.
Correto = 50%. O painel mostrava 100% porque a cancelada saía do denominador.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _cobranca(order_ref, cpf, quando, net=6050):
    return SimpleNamespace(
        id=abs(hash(order_ref)) % 100000,
        event_type="order_approved",
        order_id=order_ref,
        order_ref=order_ref,
        dedupe_key=f"import:cobranca:{order_ref}",
        subscription_id=None,
        customer_cpf=cpf,
        customer_email=f"{cpf}@example.com",
        received_at=quando,
        approved_date=quando,
        amount_net_cents=net,
        amount_gross_cents=net + 650,
        fee_cents=650,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=None,
        next_payment=None,
        charges_completed=None,
        raw_payload={},
    )


def test_cancelou_no_vencimento_conta_como_falha():
    # Alexandre: pagou 10/07 (vence 10/08) e pagou de novo 10/08 → renovou.
    alexandre_jul = _cobranca("A-JUL", "111", datetime(2026, 7, 10, tzinfo=timezone.utc))
    alexandre_ago = _cobranca("A-AGO", "111", datetime(2026, 8, 10, tzinfo=timezone.utc))
    # Girlene: pagou 10/07 (vence 10/08) e não pagou mais → não renovou.
    girlene_jul = _cobranca("G-JUL", "222", datetime(2026, 7, 10, tzinfo=timezone.utc))

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [alexandre_jul, alexandre_ago, girlene_jul]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 0.5


def test_sem_vencimento_no_mes_retorna_none():
    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [
        _cobranca("X", "111", datetime(2026, 3, 10, tzinfo=timezone.utc))
    ]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)
    assert svc.renewal_rate(2026, 8) is None


def _cancelamento(cpf, quando, motivo="não quis mais continuar"):
    return SimpleNamespace(
        id=abs(hash(f"cancel:{cpf}:{quando.isoformat()}")) % 100000,
        event_type="subscription_canceled",
        order_id=None,
        order_ref=None,
        dedupe_key=f"import:cancel:{cpf}:{quando.isoformat()}",
        subscription_id=None,
        customer_cpf=cpf,
        customer_email=f"{cpf}@example.com",
        received_at=quando,
        approved_date=None,
        amount_net_cents=0,
        amount_gross_cents=0,
        fee_cents=0,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        canceled_at=quando,
        cancel_reason=motivo,
        is_plan_change=False,
        subscription_status="canceled",
        has_access=False,
        access_until=None,
        next_payment=None,
        charges_completed=None,
        raw_payload={},
    )


def test_pagamento_seguido_de_cancelamento_real_nao_conta_como_renovacao():
    """Girlene (fictícia 2) — pagou perto do vencimento, mas cancelou de
    verdade horas depois, no mesmo ciclo. Um pagamento desfeito por um
    cancelamento real no mesmo ciclo não é renovação no sentido de negócio:
    a assinante não está continuando.

    Camila: pagou 05/07 (vence 05/08) e pagou de novo 06/08 → renovou de
    verdade, sem cancelamento.
    Patricia: pagou 06/07 (vence 06/08), pagou de novo 06/08 (dentro da
    tolerância) mas cancelou de verdade horas depois, no mesmo dia → não
    conta como renovação.
    """
    camila_jul = _cobranca("C-JUL", "333", datetime(2026, 7, 5, tzinfo=timezone.utc))
    camila_ago = _cobranca("C-AGO", "333", datetime(2026, 8, 6, tzinfo=timezone.utc))
    patricia_jul = _cobranca("P-JUL", "444", datetime(2026, 7, 6, tzinfo=timezone.utc))
    patricia_ago = _cobranca(
        "P-AGO", "444", datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    )
    patricia_cancel = _cancelamento("444", datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc))

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [
        camila_jul,
        camila_ago,
        patricia_jul,
        patricia_ago,
        patricia_cancel,
    ]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 0.5
