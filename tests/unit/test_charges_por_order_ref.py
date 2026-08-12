"""Rodada 6, item 1: cobrança única identificada por order_ref.

O array charges.completed deixa de ser fonte de cobrança — só o próprio
evento pago conta, e import + webhook da MESMA cobrança colapsam num só.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.charges import (
    charge_key,
    extract_paid_charges,
    is_import_event,
    total_paid_net,
    unknown_array_charges,
)


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        amount_net_cents=None,
        amount_gross_cents=None,
        fee_cents=None,
        approved_date=None,
        received_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_import_e_webhook_da_mesma_cobranca_viram_uma_so():
    importado = _ev(
        id=1,
        order_id="QTqDAVh",
        order_ref="QTqDAVh",
        dedupe_key="import:cobranca:QTqDAVh",
        amount_net_cents=18150,
        amount_gross_cents=19700,
        approved_date=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
    )
    webhook = _ev(
        id=2,
        order_id="c4456ec2-uuid",
        order_ref="QTqDAVh",
        dedupe_key="wh:c4456ec2",
        amount_net_cents=18150,
        amount_gross_cents=19700,
        approved_date=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
    )
    cobrancas = extract_paid_charges([importado, webhook])
    assert len(cobrancas) == 1
    assert total_paid_net([importado, webhook]) == 18150


def test_valor_do_webhook_prevalece_sobre_o_do_import():
    importado = _ev(
        id=1, order_ref="R1", dedupe_key="import:cobranca:R1", amount_net_cents=4235
    )
    webhook = _ev(id=2, order_ref="R1", dedupe_key="wh:2", amount_net_cents=6050)
    assert total_paid_net([importado, webhook]) == 6050


def test_webhook_sem_net_nao_apaga_valor_forte_do_import():
    """Finding 2 (revisão final): _better() não pode deixar origem (import vs
    webhook) vencer ANTES de checar se o vencedor tem líquido — um webhook
    cujo payload não trouxe Commissions.my_commission (net=None) não pode
    apagar um import com dado completo. Sem isso, net vira 0 e, como
    `_as_charge` cai pro preço de tabela quando bruto está ausente,
    fee_cents = max(gross - net, 0) vira o preço de tabela INTEIRO — uma
    linha de taxa absorve uma assinatura inteira, silenciosamente."""
    importado = _ev(
        id=1, order_ref="R2", dedupe_key="import:cobranca:R2", amount_net_cents=18150
    )
    webhook_sem_net = _ev(id=2, order_ref="R2", dedupe_key="wh:3", amount_net_cents=None)
    assert total_paid_net([importado, webhook_sem_net]) == 18150


def test_array_charges_completed_nao_gera_cobranca():
    """Bruna Cabral: import 42,35 + array 60,50 = 102,85 no painel. Só 42,35 é real."""
    importado = _ev(
        id=1,
        order_id="A1",
        order_ref="A1",
        dedupe_key="import:cobranca:A1",
        amount_net_cents=4235,
    )
    outro_webhook = _ev(
        id=2,
        order_ref="B2",
        dedupe_key="wh:2",
        amount_net_cents=0,
        charges_completed=[
            {"order_id": "uuid-antigo", "status": "paid", "amount": 6050}
        ],
    )
    assert total_paid_net([importado, outro_webhook]) == 4235


def test_order_approved_e_subscription_renewed_da_mesma_cobranca_contam_uma_vez():
    a = _ev(id=1, event_type="order_approved", order_ref="X", amount_net_cents=6050)
    b = _ev(id=2, event_type="subscription_renewed", order_ref="X", amount_net_cents=6050)
    assert total_paid_net([a, b]) == 6050


def test_evento_nao_pago_nao_vira_cobranca():
    cancelado = _ev(event_type="subscription_canceled", order_ref="X", amount_net_cents=6050)
    assert extract_paid_charges([cancelado]) == []


def test_evento_sem_order_ref_cai_no_order_id():
    legado = _ev(id=7, order_id="legacy-1", order_ref=None, amount_net_cents=4500)
    assert charge_key(legado) == "oid:legacy-1"
    assert total_paid_net([legado]) == 4500


def test_paid_at_usa_approved_date_e_cai_pro_received_at():
    com_approved = _ev(
        order_ref="A",
        amount_net_cents=100,
        approved_date=datetime(2026, 4, 28, tzinfo=timezone.utc),
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert extract_paid_charges([com_approved])[0]["paid_at"].month == 4

    sem_approved = _ev(
        order_ref="B",
        amount_net_cents=100,
        approved_date=None,
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert extract_paid_charges([sem_approved])[0]["paid_at"].month == 7


def test_bruto_cai_pra_tabela_do_plano_quando_ausente():
    ev = _ev(
        order_ref="A",
        amount_net_cents=13570,
        amount_gross_cents=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="trimestral",
    )
    cobranca = extract_paid_charges([ev])[0]
    assert cobranca["gross_cents"] >= cobranca["net_cents"]


def test_is_import_event():
    assert is_import_event(_ev(dedupe_key="import:cobranca:X")) is True
    assert is_import_event(_ev(dedupe_key="abc|order_approved|")) is False


def test_array_com_cobranca_desconhecida_e_reportada():
    ev = _ev(
        order_ref="A",
        amount_net_cents=100,
        charges_completed=[
            {"order_id": "conhecida", "status": "paid", "amount": 100},
            {"order_id": "fantasma", "status": "paid", "amount": 999},
            {"order_id": "nao-paga", "status": "waiting_payment", "amount": 50},
        ],
    )
    desconhecidas = unknown_array_charges([ev], known_ids={"conhecida"})
    assert [c["order_id"] for c in desconhecidas] == ["fantasma"]
