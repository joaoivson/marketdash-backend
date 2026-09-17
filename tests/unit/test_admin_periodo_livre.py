"""Filtro de período do painel admin: intervalo livre e "todo o período".

O painel só sabia somar UM mês civil. O João precisa ver o faturamento
acumulado de vários meses — ou de todos — para analisar o negócio.

O risco da mudança não é o caminho novo, é o antigo: generalizar o cálculo
podia alterar, em silêncio, o número que o painel mostra há meses. Por isso o
teste central aqui é de EQUIVALÊNCIA — intervalo de um mês tem de dar
exatamente o mesmo que o modo mensal.
"""

from datetime import date, datetime, timezone

from app.services.admin_metrics_service import (
    BRT,
    bounds_do_periodo,
    receita_das_cobrancas_no_intervalo,
    revenue_from_charges_for_month,
)


def _cobranca(quando: datetime, net: int, gross: int):
    """Evento com UMA cobrança paga, no formato que `extract_paid_charges` lê."""
    from types import SimpleNamespace

    return SimpleNamespace(
        event_type="order_approved",
        order_id=f"ord-{quando.isoformat()}",
        subscription_id="sub-1",
        customer_email="a@b.com",
        customer_cpf=None,
        received_at=quando,
        amount_net_cents=net,
        amount_gross_cents=gross,
        charges_completed=None,
        is_plan_change=False,
        refunded_at=None,
        next_payment=None,
        subscription_status=None,
        has_access=None,
        access_until=None,
        card_rejection_reason=None,
    )


# --------------------------------------------------------------------------- #
#  Limites do período                                                          #
# --------------------------------------------------------------------------- #


def test_periodo_sem_inicio_cobre_desde_antes_de_qualquer_cobranca():
    comeco, _ = bounds_do_periodo(None, date(2026, 9, 30))
    assert comeco.year == 2000


def test_periodo_sem_fim_vai_ate_agora():
    _, termino = bounds_do_periodo(date(2026, 1, 1), None)
    assert (datetime.now(timezone.utc) - termino).total_seconds() < 5


def test_limites_sao_dias_civis_de_BRASILIA_nao_de_UTC():
    """O corte é o dia civil brasileiro. Um pedido às 22h BRT do dia 30 já é 01h
    UTC do dia 31 — se o limite fosse UTC, ele cairia fora do período."""
    _, termino = bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    assert termino.astimezone(BRT).date() == date(2026, 9, 30)
    assert termino.astimezone(BRT).hour == 23

    comeco, _ = bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    assert comeco.astimezone(BRT).date() == date(2026, 9, 1)
    assert comeco.astimezone(BRT).hour == 0


# --------------------------------------------------------------------------- #
#  Equivalência — o que protege o número que já está no ar                     #
# --------------------------------------------------------------------------- #


def test_intervalo_de_um_mes_da_o_MESMO_que_o_modo_mensal():
    eventos = [
        _cobranca(datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc), 1000, 1200),  # ago
        _cobranca(datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc), 5000, 5500),  # set
        _cobranca(datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc), 3000, 3300),   # set
        _cobranca(datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc), 7000, 7700),  # out
    ]
    mensal = revenue_from_charges_for_month(eventos, 2026, 9)
    intervalo = receita_das_cobrancas_no_intervalo(
        eventos, *bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert intervalo == mensal
    assert intervalo["net"] == 8000


def test_virada_de_mes_em_BRT_cai_no_mes_certo_nos_dois_modos():
    """21h BRT do dia 30/09 = 00h UTC de 01/10. É setembro para o negócio."""
    quase_virada = datetime(2026, 10, 1, 0, 30, tzinfo=timezone.utc)  # 21h30 BRT de 30/09
    eventos = [_cobranca(quase_virada, 4200, 4600)]

    assert revenue_from_charges_for_month(eventos, 2026, 9)["net"] == 4200
    setembro = receita_das_cobrancas_no_intervalo(
        eventos, *bounds_do_periodo(date(2026, 9, 1), date(2026, 9, 30))
    )
    assert setembro["net"] == 4200
    outubro = receita_das_cobrancas_no_intervalo(
        eventos, *bounds_do_periodo(date(2026, 10, 1), date(2026, 10, 31))
    )
    assert outubro["net"] == 0


# --------------------------------------------------------------------------- #
#  Acumulado — o que o João pediu                                              #
# --------------------------------------------------------------------------- #


def test_periodo_total_soma_todos_os_meses():
    eventos = [
        _cobranca(datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc), 1000, 1100),
        _cobranca(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc), 2000, 2200),
        _cobranca(datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc), 3000, 3300),
    ]
    total = receita_das_cobrancas_no_intervalo(eventos, *bounds_do_periodo(None, None))
    assert total["net"] == 6000
    assert total["gross"] == 6600


def test_intervalo_de_varios_meses_soma_so_o_que_esta_dentro():
    eventos = [
        _cobranca(datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc), 900, 999),   # fora
        _cobranca(datetime(2026, 7, 5, 12, 0, tzinfo=timezone.utc), 1000, 1100),
        _cobranca(datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc), 2000, 2200),
        _cobranca(datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc), 8000, 8800),  # fora
    ]
    faixa = receita_das_cobrancas_no_intervalo(
        eventos, *bounds_do_periodo(date(2026, 7, 1), date(2026, 8, 31))
    )
    assert faixa["net"] == 3000


def test_periodo_vazio_nao_quebra():
    assert receita_das_cobrancas_no_intervalo([], *bounds_do_periodo(None, None)) == {
        "net": 0,
        "gross": 0,
    }
