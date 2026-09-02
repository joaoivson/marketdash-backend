"""Rodada 9 — aceites de código dos 3 itens do Luiz (25/08/2026).

Item 1: MRR mensaliza SEMPRE — "annually" da Kiwify não era reconhecido e a
assinatura anual entrava sem dividir por 12 (casos João Victor e Alice, os
R$651 de diferença). Bruto lê `product_base_price` do webhook, não a tabela.

Item 2: pareamento de upgrade compara plano NORMALIZADO — "Pro" (import) vs
"PRO - Mensal" (webhook) é o MESMO plano; comparar rótulo cru casava um
cancelamento real como upgrade (caso Bruna Cabral).
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.core.plans import _norm_freq
from app.models.subscription_event import SubscriptionEvent
from app.services.admin_metrics_service import AdminMetricsService, _freq_divisor
from app.services.subscription_event_recorder import encontrar_par_de_plan_change


# ---------------------------------------------------------------------------
# Item 1 — frequência: todos os apelidos da Kiwify mensalizam certo
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("freq", "divisor"),
    [
        ("monthly", 1),
        (None, 1),
        ("quarterly", 3),
        ("quarter", 3),
        ("trimestral", 3),
        ("yearly", 12),
        ("annual", 12),
        ("annually", 12),  # o apelido que faltava — R$651 de MRR inflado
        ("anual", 12),
        ("year", 12),
    ],
)
def test_freq_divisor_reconhece_todos_os_apelidos(freq, divisor):
    assert _freq_divisor(freq) == divisor


def test_norm_freq_annually_e_anual():
    assert _norm_freq("annually") == "anual"
    assert _norm_freq("quarter") == "trimestral"


def _assinante(**kwargs):
    defaults = dict(
        id=1,
        subscription_id="sub-jv",
        customer_email="jv@example.com",
        plan_name="PRO - Anual",
        plan_id="pro-anual",
        plan_frequency="annually",
        amount_net_cents=29241,   # líquido real recebido (my_commission)
        amount_gross_cents=44700,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_mrr_joao_victor_anual_com_afiliado():
    """Aceite 2 do Luiz: João Victor contribui R$24,37 no líquido (292,41÷12)
    e R$37,25 no bruto (447÷12) — o webhook manda freq "annually" e o preço
    base real em Commissions.product_base_price."""
    jv = _assinante(
        raw_payload={
            "order": {
                "Commissions": {
                    "charge_amount": 44700,
                    "my_commission": 29241,
                    "product_base_price": 44700,
                }
            }
        }
    )
    svc = AdminMetricsService(MagicMock())
    svc._last_paid_for = lambda ev: jv

    resultado = svc.mrr_cents(actives=[jv])

    assert resultado["net"] == 2437   # 29241 / 12 = 2436,75 -> arredonda no total
    assert resultado["gross"] == 3725  # 44700 / 12


def test_bruto_le_product_base_price_nao_tabela():
    """Venda com cupom: preço base da venda (5000) ≠ tabela do Pro mensal
    (6700). O bruto tem que ler o campo real do webhook."""
    ev = _assinante(
        plan_name="PRO - Mensal",
        plan_id="pro",
        plan_frequency="monthly",
        amount_net_cents=4000,
        raw_payload={"order": {"Commissions": {"product_base_price": 5000}}},
    )
    svc = AdminMetricsService(MagicMock())
    svc._last_paid_for = lambda ev_: ev

    resultado = svc.mrr_cents(actives=[ev])

    assert resultado["gross"] == 5000
    assert resultado["net"] == 4000


def test_bruto_sem_base_price_cai_na_tabela():
    """Import histórico não tem Commissions no raw_payload — fallback continua
    sendo o preço de tabela (comportamento anterior preservado)."""
    ev = _assinante(
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="quarterly",
        amount_net_cents=12000,
        raw_payload={"_import_source": "csv", "_row": {}},
    )
    svc = AdminMetricsService(MagicMock())
    svc._last_paid_for = lambda ev_: ev

    resultado = svc.mrr_cents(actives=[ev])

    # tabela Pro trimestral = 14700 / 3 = 4900
    assert resultado["gross"] == 4900


def test_liquido_nunca_maior_que_bruto_no_caso_afiliado():
    """Aceite 3: com a mensalização certa, líquido < bruto — a inversão do
    painel (2.670 > 2.350) era a anual sem dividir."""
    jv = _assinante(
        raw_payload={
            "order": {"Commissions": {"my_commission": 29241, "product_base_price": 44700}}
        }
    )
    svc = AdminMetricsService(MagicMock())
    svc._last_paid_for = lambda ev: jv
    resultado = svc.mrr_cents(actives=[jv])
    assert resultado["net"] < resultado["gross"]


def test_distribuicao_plano_periodicidade_annually_vai_pro_anual():
    ev = _assinante()
    svc = AdminMetricsService(MagicMock())
    svc.renewing_subscribers = lambda: [ev]
    svc._last_paid_for = lambda ev_: ev

    dist = svc.plan_frequency_distribution()

    assert len(dist) == 1
    assert dist[0]["plan"] == "pro"
    assert dist[0]["frequency"] == "anual"


# ---------------------------------------------------------------------------
# Item 2 — pareamento de upgrade compara plano normalizado
# ---------------------------------------------------------------------------

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


_next_id = [1000]


def _grava(db, **kwargs):
    _next_id[0] += 1
    padrao = dict(
        id=_next_id[0],
        event_type="order_approved",
        customer_cpf="12345678900",
        plan_name="PRO - Mensal",
        plan_id="pro",
        plan_frequency="monthly",
        received_at=datetime(2026, 8, 3, 23, 28, tzinfo=timezone.utc),
        raw_payload={},
        dedupe_key=f"r9-{_next_id[0]}",
        is_plan_change=False,
    )
    padrao.update(kwargs)
    db.add(SubscriptionEvent(**padrao))
    db.commit()


def test_cancelamento_do_mesmo_plano_com_rotulo_diferente_nao_e_upgrade(db):
    """Caso Bruna Cabral: assinou (import grava "Pro", webhook "PRO - Mensal")
    e cancelou o MESMO plano 21 dias depois. Rótulos crus diferentes faziam o
    pareamento enxergar "plano diferente ≤30d" e casar como upgrade — o
    cancelamento real sumia do churn."""
    _grava(db, plan_name="Pro", received_at=datetime(2026, 8, 3, 23, 28, 32, tzinfo=timezone.utc))
    _grava(db, plan_name="PRO - Mensal", received_at=datetime(2026, 8, 3, 23, 28, 35, tzinfo=timezone.utc))

    cancel = {
        "event_type": "subscription_canceled",
        "customer_cpf": "12345678900",
        "plan_name": "PRO - Mensal",
        "plan_id": "pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        db, cancel, reference_time=datetime(2026, 8, 24, 19, 18, tzinfo=timezone.utc)
    )
    assert par is None


def test_upgrade_de_verdade_continua_pareando(db):
    """Caso Ana Ariel: cancelou a ESSENCIAL minutos depois de assinar a PRO —
    planos normalizados DIFERENTES dentro da janela seguem casando."""
    _grava(db, plan_name="PRO - Mensal", plan_id="pro",
           received_at=datetime(2026, 8, 10, 14, 20, 31, tzinfo=timezone.utc))

    cancel = {
        "event_type": "subscription_canceled",
        "customer_cpf": "12345678900",
        "plan_name": "ESSENCIAL - Mensal",
        "plan_id": "essencial",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        db, cancel, reference_time=datetime(2026, 8, 10, 14, 24, 1, tzinfo=timezone.utc)
    )
    assert par is not None


def test_continuacao_mesmo_plano_em_ate_um_dia_segue_valendo(db):
    """Troca de forma de pagamento (cancela e repaga o mesmo plano em minutos,
    cancelamento ANTES do pagamento) continua sendo continuação, não churn."""
    _grava(db, plan_name="PRO - Mensal",
           received_at=datetime(2026, 8, 10, 14, 30, tzinfo=timezone.utc))

    cancel = {
        "event_type": "subscription_canceled",
        "customer_cpf": "12345678900",
        "plan_name": "Pro",  # rótulo do import — mesmo plano normalizado
        "plan_id": "pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        db, cancel, reference_time=datetime(2026, 8, 10, 14, 25, tzinfo=timezone.utc)
    )
    assert par is not None
