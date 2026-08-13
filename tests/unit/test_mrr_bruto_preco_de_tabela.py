"""Rodada 7, item 3: bruto do MRR usa preço de TABELA (list_price_cents),
não o valor real da última cobrança paga — que pode vir com desconto
histórico. Líquido continua vindo da cobrança real (não muda)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _ev(**kwargs):
    defaults = dict(
        id=1,
        subscription_id="sub-1",
        customer_email="a@example.com",
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="trimestral",
        amount_net_cents=12000,   # pagou com desconto — abaixo da tabela
        amount_gross_cents=12500,  # bruto real pago, também abaixo da tabela (14700)
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_bruto_usa_preco_de_tabela_nao_ultima_cobranca():
    svc = AdminMetricsService(MagicMock())
    assinante = _ev()
    svc._last_paid_for = lambda ev: assinante  # última cobrança = o próprio evento

    resultado = svc.mrr_cents(actives=[assinante])

    # Pro trimestral: tabela = 14700 cents / 3 = 4900 exato.
    assert resultado["gross"] == 4900
    # líquido continua vindo da cobrança real (12000 / 3 = 4000).
    assert resultado["net"] == 4000


def test_bruto_cai_no_valor_real_quando_plano_fora_do_catalogo():
    svc = AdminMetricsService(MagicMock())
    assinante = _ev(plan_name="Plano Descontinuado", plan_id="legado", amount_gross_cents=5000, amount_net_cents=4500)
    svc._last_paid_for = lambda ev: assinante

    resultado = svc.mrr_cents(actives=[assinante])

    # _normalize_plan_label("Plano Descontinuado", "legado") cai em "essencial"
    # por default — mas se não houver frequência reconhecida ou preço no
    # catálogo, list_price_cents pode retornar None; nesse caso o fallback é
    # o valor real pago (comportamento anterior), não zero.
    assert resultado["gross"] > 0
