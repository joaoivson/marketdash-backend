"""
MRR histórico reconstruído a partir das cobranças.

Antes o gráfico repetia o MRR de hoje em todos os meses, porque
`active_subscribers(as_of)` pegava o ÚLTIMO evento de cada assinante
independentemente da data. Agora cada cobrança paga gera um período de vigência
(data + 1/3/12 meses) e o MRR do mês é a soma de quem estava coberto no último
dia dele.

Fixture = dados reais de produção em 03/08/2026.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.admin_metrics_service import build_coverage_periods, _add_months


def _ev(email, plano, freq, cobrancas, access_until=None, tipo="order_approved",
        recebido="2026-08-01T00:00:00Z", refunded_at=None):
    """Evento com charges_completed no formato que a Kiwify manda."""
    return SimpleNamespace(
        customer_email=email,
        subscription_id=email,
        plan_name=plano,
        plan_id=None,
        plan_frequency=freq,
        event_type=tipo,
        amount_net_cents=None,
        amount_gross_cents=None,
        access_until=datetime.fromisoformat(access_until) if access_until else None,
        refunded_at=datetime.fromisoformat(refunded_at) if refunded_at else None,
        received_at=datetime.fromisoformat(recebido.replace("Z", "+00:00")),
        charges_completed=[
            {
                "order_id": f"{email}-{i}",
                "status": "paid",
                "approved_date": pago,
                "Commissions": {"my_commission": liquido},
            }
            for i, (pago, liquido) in enumerate(cobrancas)
        ],
        raw_payload={},
    )


# Produção em 03/08/2026
EVENTOS = [
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
    """Conferência da regra: o fim calculado tem que bater com o que a Kiwify manda."""
    periodos = build_coverage_periods(EVENTOS)
    for ev in EVENTOS:
        ps = periodos[f"sub:{ev.subscription_id}"]
        assert ps, f"{ev.customer_email} sem período"
        assert ps[-1]["fim"] == ev.access_until, (
            f"{ev.customer_email}: calculado {ps[-1]['fim']} != Kiwify {ev.access_until}"
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
    eventos = [
        _ev("x@y.com", "PRO - Mensal", "monthly", [("2026-05-10T00:00:00Z", 6050)]),
        _ev("x@y.com", "PRO - Mensal", "monthly", [("2026-05-10T00:00:00Z", 6050)],
            tipo="order_refunded", refunded_at="2026-05-20T00:00:00+00:00",
            recebido="2026-05-20T00:00:00Z"),
    ]
    assert _mrr("2026-05-15T00:00:00+00:00", eventos) == 6050   # antes do reembolso
    assert _mrr("2026-05-25T00:00:00+00:00", eventos) == 0      # depois
    # e não retroage: o mês da venda segue contando até o dia do reembolso


def test_trimestral_cobre_tres_meses():
    eventos = [_ev("t@y.com", "PRO - Trimestral", "quarterly",
                   [("2026-01-15T00:00:00Z", 13570)])]
    assert _mrr(fim_do_mes(2026, 1, 31), eventos) == 4523
    assert _mrr(fim_do_mes(2026, 2, 28), eventos) == 4523
    assert _mrr(fim_do_mes(2026, 3, 31), eventos) == 4523
    assert _mrr(fim_do_mes(2026, 4, 30), eventos) == 0  # venceu 15/04
