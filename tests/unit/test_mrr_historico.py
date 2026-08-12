"""
MRR histórico reconstruído a partir das cobranças.

Antes o gráfico repetia o MRR de hoje em todos os meses, porque
`active_subscribers(as_of)` pegava o ÚLTIMO evento de cada assinante
independentemente da data. Agora cada cobrança paga gera um período de vigência
(data + 1/3/12 meses) e o MRR do mês é a soma de quem estava coberto no último
dia dele.

Fixture = dados reais de produção em 03/08/2026.

Rodada 6 item 1: uma cobrança é um EVENTO pago com `order_ref`/`amount_net_cents`/
`approved_date` no topo (é assim que `subscription_event_recorder.py` grava um
webhook de verdade) — não mais um evento carregando um array `charges_completed`
com N cobranças. Cada cobrança da fixture agora é o seu próprio evento
(order_approved na primeira, subscription_renewed nas seguintes), e o estado mais
recente do assinante (late/cancelado/access_until) é um evento à parte, como a
Kiwify manda de verdade.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.admin_metrics_service import build_coverage_periods, _add_months


def _iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _charge_ev(email, plano, freq, order_ref, aprovado_iso, liquido, event_type="order_approved"):
    """Um evento de cobrança de verdade — order_ref/amount_net_cents/approved_date
    no topo do evento (ver subscription_event_recorder.py), não num array."""
    aprovado = _iso(aprovado_iso)
    return SimpleNamespace(
        customer_email=email,
        subscription_id=email,
        plan_name=plano,
        plan_id=None,
        plan_frequency=freq,
        event_type=event_type,
        order_ref=order_ref,
        order_id=order_ref,
        amount_net_cents=liquido,
        amount_gross_cents=None,
        access_until=None,
        refunded_at=None,
        received_at=aprovado,
        approved_date=aprovado,
        charges_completed=None,
        raw_payload={},
    )


def _state_ev(email, event_type, recebido_iso, access_until=None, refunded_at=None):
    """Evento de mudança de estado (late/cancelado/reembolso) — sempre depois da
    última cobrança, sem cobrança própria (sem order_ref)."""
    return SimpleNamespace(
        customer_email=email,
        subscription_id=email,
        plan_name=None,
        plan_id=None,
        plan_frequency=None,
        event_type=event_type,
        order_ref=None,
        order_id=None,
        amount_net_cents=None,
        amount_gross_cents=None,
        access_until=_iso(access_until),
        refunded_at=_iso(refunded_at),
        received_at=_iso(recebido_iso),
        approved_date=None,
        charges_completed=None,
        raw_payload={},
    )


def _ev(email, plano, freq, cobrancas, access_until=None, tipo="order_approved",
        recebido=None, refunded_at=None):
    """Um assinante = uma lista de eventos: uma cobrança por entrada de `cobrancas`
    (a primeira order_approved, as seguintes subscription_renewed) + um evento de
    estado final carregando access_until/tipo/refunded_at — como a Kiwify manda.

    `recebido`, se não informado, é 1 microssegundo depois da última cobrança —
    o evento de estado é sempre o mais recente pra `build_coverage_periods` usar
    o access_until dele."""
    eventos = [
        _charge_ev(
            email, plano, freq, f"{email}-{i}", pago, liquido,
            event_type="order_approved" if i == 0 else "subscription_renewed",
        )
        for i, (pago, liquido) in enumerate(cobrancas)
    ]
    if recebido is not None:
        estado_recebido = recebido
    else:
        ultima = max(
            (e.received_at for e in eventos),
            default=datetime(2026, 8, 1, tzinfo=timezone.utc),
        )
        estado_recebido = (ultima + timedelta(microseconds=1)).isoformat()
    eventos.append(
        _state_ev(email, tipo, estado_recebido, access_until=access_until, refunded_at=refunded_at)
    )
    return eventos


def _flatten(grupos):
    return [ev for grupo in grupos for ev in grupo]


# Produção em 03/08/2026
EVENTOS = _flatten([
    _ev("bas_alves@hotmail.com", "PRO - Trimestral", "quarterly",
        [("2026-04-28T09:10:48.195Z", 13570)],
        access_until="2026-07-28T09:10:51.865+00:00", tipo="subscription_late"),
    _ev("leticiaopuchkevich2006@gmail.com", "PRO - Mensal", "monthly",
        [("2026-05-26T01:00:51.938Z", 6050),
         ("2026-06-25T08:55:25.290Z", 6050),
         ("2026-07-25T06:15:53.742Z", 6050)],
        access_until="2026-08-25T06:15:55.263+00:00"),
    _ev("nina_bonzatto@hotmail.com", "PRO - Mensal", "monthly",
        [("2026-05-30T20:23:53.434Z", 6050),
         ("2026-06-30T08:55:42.210Z", 6050),
         ("2026-07-30T05:45:19.144Z", 6050)],
        access_until="2026-08-30T05:45:21.156+00:00"),
    _ev("fael-e.hf@hotmail.com", "PRO - Mensal", "monthly",
        [("2026-06-29T16:18:47.936Z", 6050)],
        access_until="2026-07-29T16:19:25.295+00:00", tipo="subscription_late"),
    _ev("deivitrafael56@gmail.com", "ESSENCIAL - Mensal", "monthly",
        [("2026-07-31T22:05:24.982Z", 4169)],
        access_until="2026-08-31T22:06:53.212+00:00", tipo="subscription_canceled"),
    _ev("rubianekrisca@hotmail.com", "PRO - Mensal", "monthly",
        [("2026-08-02T18:23:02.239Z", 6050)],
        access_until="2026-09-02T18:23:04.396+00:00"),
])

SUBSCRIBER_EMAILS = [
    "bas_alves@hotmail.com",
    "leticiaopuchkevich2006@gmail.com",
    "nina_bonzatto@hotmail.com",
    "fael-e.hf@hotmail.com",
    "deivitrafael56@gmail.com",
    "rubianekrisca@hotmail.com",
]


def _mrr(momento_iso, eventos=None):
    from app.services.admin_metrics_service import AdminMetricsService

    svc = AdminMetricsService.__new__(AdminMetricsService)  # sem DB
    periodos = build_coverage_periods(eventos if eventos is not None else EVENTOS)
    return svc.mrr_at(datetime.fromisoformat(momento_iso), periodos)["net"]


def fim_do_mes(ano, mes, ultimo_dia):
    return f"{ano}-{mes:02d}-{ultimo_dia:02d}T23:59:59+00:00"


# --- soma de meses ---------------------------------------------------------------


def test_soma_meses_preserva_o_dia():
    d = datetime(2026, 4, 28, 9, 10, tzinfo=timezone.utc)
    assert _add_months(d, 3) == datetime(2026, 7, 28, 9, 10, tzinfo=timezone.utc)


def test_soma_meses_encurta_quando_o_mes_e_menor():
    d = datetime(2026, 1, 31, tzinfo=timezone.utc)
    assert _add_months(d, 1).day == 28  # fevereiro


def test_soma_meses_vira_o_ano():
    d = datetime(2026, 8, 2, tzinfo=timezone.utc)
    assert _add_months(d, 12) == datetime(2027, 8, 2, tzinfo=timezone.utc)


# --- a regra reproduz o access_until da Kiwify -----------------------------------


def test_vigencia_calculada_bate_com_access_until_de_todos():
    """Conferência da regra: o fim calculado tem que bater com o que a Kiwify manda
    (o evento de estado final de cada assinante, último por received_at)."""
    periodos = build_coverage_periods(EVENTOS)
    for email in SUBSCRIBER_EMAILS:
        ultimo_estado = max(
            (ev for ev in EVENTOS if ev.customer_email == email),
            key=lambda ev: ev.received_at,
        )
        ps = periodos[f"sub:{email}"]
        assert ps, f"{email} sem período"
        assert ps[-1]["fim"] == ultimo_estado.access_until, (
            f"{email}: calculado {ps[-1]['fim']} != Kiwify {ultimo_estado.access_until}"
        )


# --- MRR mês a mês ---------------------------------------------------------------


def test_abril_so_a_trimestral():
    # Bruna: 13570 trimestral → 13570 // 3 = 4523
    assert _mrr(fim_do_mes(2026, 4, 30)) == 4523


def test_maio_trimestral_mais_duas_mensais():
    # Bruna 4523 + Letícia 6050 (26/05) + Irina 6050 (30/05)
    assert _mrr(fim_do_mes(2026, 5, 31)) == 4523 + 6050 + 6050


def test_junho_soma_o_fael():
    # Bruna 4523 + Letícia + Irina + fael (29/06)
    assert _mrr(fim_do_mes(2026, 6, 30)) == 4523 + 6050 * 3


def test_julho_perde_bruna_e_fael_e_ganha_deivit():
    # Bruna vence 28/07 e fael 29/07; Deivit entra 31/07 22:05
    assert _mrr(fim_do_mes(2026, 7, 31)) == 6050 + 6050 + 4169


def test_agosto_retrato_de_hoje():
    # Letícia + Irina + Deivit (cancelado c/ acesso) + Rubiane
    assert _mrr("2026-08-03T12:00:00+00:00") == 6050 + 6050 + 4169 + 6050 == 22319


def test_nao_repete_o_valor_de_hoje_para_tras():
    """O bug original: todo mês mostrava 22319."""
    hoje = _mrr("2026-08-03T12:00:00+00:00")
    for mes, ultimo in ((4, 30), (5, 31), (6, 30), (7, 31)):
        assert _mrr(fim_do_mes(2026, mes, ultimo)) != hoje


# --- reembolso -------------------------------------------------------------------


def test_reembolso_encerra_a_vigencia_na_data_do_reembolso():
    eventos = _flatten([
        _ev("x@y.com", "PRO - Mensal", "monthly", [("2026-05-10T00:00:00Z", 6050)],
            tipo="order_refunded", refunded_at="2026-05-20T00:00:00+00:00",
            recebido="2026-05-20T00:00:00Z"),
    ])
    assert _mrr("2026-05-15T00:00:00+00:00", eventos) == 6050   # antes do reembolso
    assert _mrr("2026-05-25T00:00:00+00:00", eventos) == 0      # depois
    # e não retroage: o mês da venda segue contando até o dia do reembolso


def test_trimestral_cobre_tres_meses():
    eventos = _flatten([
        _ev("t@y.com", "PRO - Trimestral", "quarterly",
            [("2026-01-15T00:00:00Z", 13570)],
            access_until="2026-04-15T00:00:00+00:00"),
    ])
    assert _mrr(fim_do_mes(2026, 1, 31), eventos) == 4523
    assert _mrr(fim_do_mes(2026, 2, 28), eventos) == 4523
    assert _mrr(fim_do_mes(2026, 3, 31), eventos) == 4523
    assert _mrr(fim_do_mes(2026, 4, 30), eventos) == 0  # venceu 15/04
