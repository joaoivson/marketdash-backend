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


def _cancelamento(cpf, quando, motivo="não quis mais continuar", is_plan_change=False):
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
        is_plan_change=is_plan_change,
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


def test_cancelamento_semanas_depois_no_mesmo_mes_nao_desfaz_renovacao():
    """Finding 1: a janela do check de cancelamento precisa ficar restrita ao
    CICLO da assinante (venceu_em + tolerância), não ao mês inteiro.

    Fabiana: período vence 02/08, paga no mesmo dia (renovação genuína, nova
    vigência até 02/09). Cancela de verdade só em 28/08 — semanas depois, sem
    relação com a renovação de 02/08. Isso é churn do ciclo/mês em que ela
    de fato cancelou (via churn_for_month), não uma reversão da renovação de
    02/08. Com a janela antiga (upper bound = fim do mês/agora), esse
    cancelamento de 28/08 seria capturado erroneamente e derrubaria a
    renovação para "não renovou". Com a janela corrigida
    (min(end, venceu_em + tolerância) = 05/08), o cancelamento de 28/08 fica
    de fora e ela continua contando como renovada.
    """
    fabiana_jul = _cobranca("F-JUL", "555", datetime(2026, 7, 2, tzinfo=timezone.utc))
    fabiana_ago = _cobranca("F-AGO", "555", datetime(2026, 8, 2, tzinfo=timezone.utc))
    fabiana_cancel = _cancelamento("555", datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc))

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [fabiana_jul, fabiana_ago, fabiana_cancel]
    # Mês de agosto já fechado (agora em setembro) — sem isso, `end` nem
    # chegaria perto de 28/08 e o teste não exerceria o bug.
    svc._agora = lambda: datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 1.0


def test_cancelamento_troca_de_plano_no_ciclo_nao_desfaz_renovacao():
    """Finding 2a: cancelamento de troca de plano (is_plan_change=True) dentro
    da janela do ciclo não pode contar como cancelamento real — cancel_instants()
    já exclui esse caso, e renewal_rate() precisa reaproveitar essa exclusão
    (não reimplementar um check inline que ignore is_plan_change)."""
    isabela_jul = _cobranca("I-JUL", "666", datetime(2026, 7, 6, tzinfo=timezone.utc))
    isabela_ago = _cobranca(
        "I-AGO", "666", datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    )
    isabela_cancel_upgrade = _cancelamento(
        "666", datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc), is_plan_change=True
    )

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [isabela_jul, isabela_ago, isabela_cancel_upgrade]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 1.0


def test_upgrade_com_vencimento_no_mes_conta_como_renovada():
    """Finding 1 (revisão final): a cliente que fez upgrade tem duas
    subscriber_keys (a Kiwify atribui um subscription_id NOVO no upgrade) —
    Essencial (sub-ess-1) e Pro (sub-pro-1), mesmo CPF.

    A vigência Essencial vence dentro do mês medido. O cancelamento da
    Essencial é o lado superado do upgrade (is_plan_change=True) — já
    corretamente excluído de cancel_instants(), então `cancelou_no_ciclo`
    dá False. Mas o PAGAMENTO da renovação-por-upgrade caiu sob a chave NOVA
    (sub-pro-1), pago ANTES do vencimento da Essencial e dentro da janela de
    tolerância — sem olhar pra essa outra chave, `pagou` também dá False e
    ela é contada como renovação FALHA, quando na verdade é uma cliente
    contínua e pagante que só trocou de plano.
    """
    essencial_jul = _cobranca(
        "E-JUL", "888", datetime(2026, 7, 6, tzinfo=timezone.utc), net=4235
    )
    essencial_jul.subscription_id = "sub-ess-1"
    essencial_cancel_upgrade = _cancelamento(
        "888", datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc), is_plan_change=True
    )
    essencial_cancel_upgrade.subscription_id = "sub-ess-1"
    # Pago sob a chave NOVA (sub-pro-1) — 9h depois do vencimento da Essencial
    # (06/08 = 06/07 + 1 mês), dentro da tolerância de 3 dias.
    pro_pago_upgrade = _cobranca(
        "PRO-AGO", "888", datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc), net=6050
    )
    pro_pago_upgrade.subscription_id = "sub-pro-1"
    pro_pago_upgrade.is_plan_change = True

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [
        essencial_jul,
        essencial_cancel_upgrade,
        pro_pago_upgrade,
    ]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 1.0


def test_cancelamento_real_da_chave_nova_do_upgrade_desfaz_renovacao():
    """Achado (revisão final, rodada 6): o broadening de `pagou` pra chave
    NOVA do upgrade (teste acima) não tinha o espelho em `cancelou_no_ciclo`
    — um cancelamento real da chave NOVA ficava invisível pro check, porque
    ele só olhava `cancelamentos.get(chave_devida, [])` (a chave ANTIGA).

    Denise: assinatura Essencial (sub:s-d-old) vence em 06/08. O upgrade pra
    Pro (sub:s-d-new, is_plan_change=True) paga dentro da tolerância — isso
    por si só já contaria como renovação (regra do teste acima). Mas horas
    depois, no mesmo dia, ela cancela de verdade a Pro (is_plan_change=False)
    — mesma regra da Patricia/Girlene (pagamento desfeito por cancelamento
    real no mesmo ciclo não é renovação), só que a cobrança E o cancelamento
    estão os dois na chave nova. `cancelou_no_ciclo` precisa olhar pra essa
    chave também, não só pra `sub:s-d-old`.
    """
    denise_essencial_jul = _cobranca(
        "D-ESS-JUL", "999", datetime(2026, 7, 6, tzinfo=timezone.utc), net=4235
    )
    denise_essencial_jul.subscription_id = "s-d-old"
    denise_cancel_upgrade = _cancelamento(
        "999", datetime(2026, 8, 6, 10, 0, tzinfo=timezone.utc), is_plan_change=True
    )
    denise_cancel_upgrade.subscription_id = "s-d-old"
    denise_pro_pago_upgrade = _cobranca(
        "D-PRO-AGO", "999", datetime(2026, 8, 6, 9, 0, tzinfo=timezone.utc), net=6050
    )
    denise_pro_pago_upgrade.subscription_id = "s-d-new"
    denise_pro_pago_upgrade.is_plan_change = True
    denise_cancel_real = _cancelamento(
        "999", datetime(2026, 8, 6, 18, 0, tzinfo=timezone.utc), is_plan_change=False
    )
    denise_cancel_real.subscription_id = "s-d-new"

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [
        denise_essencial_jul,
        denise_cancel_upgrade,
        denise_pro_pago_upgrade,
        denise_cancel_real,
    ]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 0.0


def test_cancelamento_ajuste_produtor_no_ciclo_nao_desfaz_renovacao():
    """Finding 2b: cancelamento com cancel_reason="cancelado pelo produtor"
    dentro da janela do ciclo não é churn real (ajuste administrativo) —
    cancel_instants() já exclui esse motivo, e renewal_rate() precisa
    reaproveitar essa exclusão."""
    monica_jul = _cobranca("M-JUL", "777", datetime(2026, 7, 6, tzinfo=timezone.utc))
    monica_ago = _cobranca(
        "M-AGO", "777", datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)
    )
    monica_cancel_produtor = _cancelamento(
        "777",
        datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc),
        motivo="cancelado pelo produtor",
    )

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [monica_jul, monica_ago, monica_cancel_produtor]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 1.0
