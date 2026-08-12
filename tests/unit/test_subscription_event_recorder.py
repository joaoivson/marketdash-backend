"""Unit tests for subscription_event_recorder."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.models.subscription_event import SubscriptionEvent
from app.models.user import User
from app.services.subscription_event_recorder import (
    build_dedupe_key,
    extract_event_fields,
    record_subscription_event,
    _as_cents,
    encontrar_par_de_plan_change,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json_on_sqlite(type_, compiler, **kw):
    """SubscriptionEvent tem colunas JSONB (Postgres-only) — SQLite em memória
    (usado nos testes) não sabe compilar esse tipo. Renderiza como JSON
    genérico, que o dialeto sqlite entende (afinidade TEXT)."""
    return "JSON"


def test_as_cents():
    assert _as_cents(14700) == 14700
    assert _as_cents("1130") == 1130
    assert _as_cents(None) is None


def test_dedupe_key_stable():
    k1 = build_dedupe_key("ord1", "order_approved", None)
    k2 = build_dedupe_key("ord1", "order_approved", None)
    assert k1 == k2
    assert "order_approved" in k1


def test_extract_unknown_event_type():
    payload = {
        "order": {
            "order_id": "abc",
            "webhook_event_type": "evento_futuro_xyz",
            "Customer": {"email": "a@b.com", "full_name": "A", "CPF": "123"},
            "Commissions": {"charge_amount": 4700, "kiwify_fee": 300, "my_commission": 4400},
            "Subscription": {
                "status": "active",
                "plan": {"name": "Essencial", "frequency": "monthly"},
                "customer_access": {"has_access": True, "access_until": "2030-01-01"},
            },
        }
    }
    fields = extract_event_fields(payload, "evento_futuro_xyz")
    assert fields["event_type"] == "evento_futuro_xyz"
    assert fields["amount_gross_cents"] == 4700
    assert fields["amount_net_cents"] == 4400
    assert fields["customer_email"] == "a@b.com"
    assert fields["has_access"] is True


def test_canceled_with_access_still_parsed():
    payload = {
        "order": {
            "order_id": "c1",
            "webhook_event_type": "subscription_canceled",
            "Customer": {"email": "x@y.com"},
            "Subscription": {
                "status": "canceled",
                "plan": {"name": "Pro", "frequency": "quarterly"},
                "customer_access": {"has_access": True, "access_until": "10/10/2026"},
            },
            "Commissions": {},
        }
    }
    fields = extract_event_fields(payload, "subscription_canceled")
    assert fields["has_access"] is True
    assert fields["subscription_status"] == "canceled"
    assert fields["access_until"] is not None


def test_extract_card_rejection_reason():
    payload = {
        "order": {
            "order_id": "late1",
            "card_rejection_reason": "refused_bank",
            "Customer": {"email": "late@ex.com"},
            "Subscription": {"status": "waiting_payment", "plan": {"name": "Essencial"}},
            "Commissions": {},
        }
    }
    fields = extract_event_fields(payload, "subscription_late")
    assert fields["card_rejection_reason"] == "refused_bank"
    assert fields["subscription_status"] == "waiting_payment"


def test_extract_card_rejection_reason_top_level():
    """Kiwify may put card_rejection_reason on the webhook root, not inside order."""
    payload = {
        "card_rejection_reason": "insufficient_funds",
        "order": {
            "order_id": "late2",
            "Customer": {"email": "late2@ex.com"},
            "Subscription": {"status": "waiting_payment", "plan": {"name": "Essencial"}},
            "Commissions": {},
        },
    }
    fields = extract_event_fields(payload, "subscription_late")
    assert fields["card_rejection_reason"] == "insufficient_funds"


# --- encontrar_par_de_plan_change (continuação/upgrade, item 12 / Parte B.2) ------


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    SubscriptionEvent.__table__.create(engine)
    User.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()

    # BigInteger PK não ganha o alias de rowid/autoincrement do SQLite (só
    # Integer puro ganha) — em produção o Postgres resolve via BIGSERIAL; aqui
    # precisamos atribuir manualmente pros inserts feitos pelo próprio código
    # de produção (record_subscription_event não passa id, como no webhook real).
    contador = [0]

    @event.listens_for(SubscriptionEvent, "before_insert")
    def _atribuir_id(mapper, connection, target):
        if target.id is None:
            contador[0] += 1
            target.id = contador[0]

    yield sessao
    sessao.close()
    event.remove(SubscriptionEvent, "before_insert", _atribuir_id)


def _cancel(db, cpf, received_at, plan_name="Pro", plan_frequency="monthly"):
    ev = SubscriptionEvent(
        event_type="subscription_canceled",
        customer_cpf=cpf,
        plan_name=plan_name,
        plan_frequency=plan_frequency,
        received_at=received_at,
        raw_payload={},
        dedupe_key=f"k-{received_at.isoformat()}",
    )
    db.add(ev)
    db.commit()


def test_continuacao_mesmo_plano_ate_1_dia(db):
    """Caso Luiz 15/06: cancela e reassina o MESMO plano no mesmo instante."""
    cancel_em = datetime(2026, 6, 15, 10, 50, tzinfo=timezone.utc)
    _cancel(db, "111", cancel_em, plan_name="Pro", plan_frequency="monthly")
    fields = {
        "event_type": "order_approved", "customer_cpf": "111",
        "plan_name": "Pro", "plan_frequency": "monthly",
    }
    novo_pagamento = cancel_em  # instante exato
    par = encontrar_par_de_plan_change(db, fields, reference_time=novo_pagamento)
    assert par is not None
    assert par.customer_cpf == "111"


def test_upgrade_plano_diferente_ate_30_dias(db):
    """Caso Cristiana: yearly cancela, quarterly começa 9 dias depois."""
    cancel_em = datetime(2026, 5, 22, 0, 0, tzinfo=timezone.utc)
    _cancel(db, "222", cancel_em, plan_name="Pro", plan_frequency="yearly")
    fields = {
        "event_type": "order_approved", "customer_cpf": "222",
        "plan_name": "Pro", "plan_frequency": "quarterly",
    }
    novo_pagamento = datetime(2026, 5, 31, 0, 0, tzinfo=timezone.utc)  # 9 dias depois
    par = encontrar_par_de_plan_change(db, fields, reference_time=novo_pagamento)
    assert par is not None
    assert par.customer_cpf == "222"


def test_mesmo_plano_mas_gap_maior_que_1_dia_nao_conta(db):
    """Gap de 2 dias, mesmo plano — nem continuação (>1 dia) nem upgrade (plano igual)."""
    cancel_em = datetime(2026, 4, 10, 18, 35, tzinfo=timezone.utc)
    _cancel(db, "333", cancel_em, plan_name="Pro", plan_frequency="monthly")
    fields = {
        "event_type": "order_approved", "customer_cpf": "333",
        "plan_name": "Pro", "plan_frequency": "monthly",
    }
    novo_pagamento = datetime(2026, 4, 12, 12, 7, tzinfo=timezone.utc)  # ~41h depois
    assert encontrar_par_de_plan_change(db, fields, reference_time=novo_pagamento) is None


def test_reference_time_ancora_a_janela_no_evento_historico_nao_em_agora(db):
    """Sem reference_time explícito, um evento de abril (processado hoje, muito
    depois de 30 dias atrás) nunca dispararia a regra — o bug que motivou o
    parâmetro. Com reference_time = data do próprio evento, funciona."""
    cancel_em = datetime(2026, 4, 7, 20, 36, tzinfo=timezone.utc)
    _cancel(db, "444", cancel_em, plan_name="Pro", plan_frequency="monthly")
    fields = {
        "event_type": "order_approved", "customer_cpf": "444",
        "plan_name": "Diferente", "plan_frequency": "monthly",
    }
    novo_pagamento = datetime(2026, 4, 9, 0, 0, tzinfo=timezone.utc)
    # Sem reference_time (usa datetime.now() real, muito distante de abril/2026) — não dispara.
    assert encontrar_par_de_plan_change(db, fields) is None
    # Com reference_time ancorado no evento histórico — dispara (upgrade, plano diferente, <30d).
    par = encontrar_par_de_plan_change(db, fields, reference_time=novo_pagamento)
    assert par is not None
    assert par.customer_cpf == "444"


def test_cancel_sem_par_nao_dispara(db):
    fields = {
        "event_type": "order_approved", "customer_cpf": "555",
        "plan_name": "Pro", "plan_frequency": "monthly",
    }
    assert encontrar_par_de_plan_change(db, fields, reference_time=datetime.now(timezone.utc)) is None


def _paid(db, cpf, received_at, plan_name="Pro", plan_frequency="monthly", event_type="order_approved"):
    ev = SubscriptionEvent(
        event_type=event_type,
        customer_cpf=cpf,
        plan_name=plan_name,
        plan_frequency=plan_frequency,
        received_at=received_at,
        raw_payload={},
        dedupe_key=f"k-paid-{received_at.isoformat()}-{cpf}",
    )
    db.add(ev)
    db.commit()
    return ev


def test_cancelamento_chegando_encontra_pagamento_anterior_sessao_real(db):
    """Review do Task 5: as 5 tests DB-backed existentes só cobrem 'evento pago
    chega, procura cancelamento'. Esse aqui cobre a direção que faltava — o caso
    real da Ana Ariel: o CANCELAMENTO chega e precisa achar o PAGAMENTO anterior
    (não o outro cancelamento) numa sessão SQLite de verdade, não em mock."""
    cpf = "666"
    pagamento_em = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    pago = _paid(db, cpf, pagamento_em, plan_name="Pro", plan_frequency="monthly")

    fields = {
        "event_type": "subscription_canceled",
        "customer_cpf": cpf,
        "plan_name": "Essencial",
        "plan_frequency": "monthly",
    }
    # Cancelamento da Essencial chega 8 minutos DEPOIS do pagamento da Pro —
    # o timeline exato da Ana Ariel.
    par = encontrar_par_de_plan_change(
        db, fields, reference_time=pagamento_em + timedelta(minutes=8)
    )
    assert par is not None
    assert par.id == pago.id
    assert par.event_type == "order_approved"
    assert par.plan_name == "Pro"
    assert par.customer_cpf == cpf


def test_cancelamento_depois_de_pagamento_mesmo_plano_nao_e_continuacao(db):
    """Fix do item 5b: renovação normal seguida de cancelamento de verdade não
    pode virar 'continuação' só porque caiu dentro de ≤1 dia e é o mesmo plano.

    Caso real (nomes trocados por dados sintéticos): cliente fictícia renova o
    plano Pro às 04:46 e cancela a MESMA assinatura às 18:19 do mesmo dia — um
    cancelamento genuíno horas depois da renovação, não uma troca de forma de
    pagamento. A branch de mesmo-plano só pode formar par quando o CANCELAMENTO
    aconteceu ANTES (ou no mesmo instante) do PAGAMENTO — nunca o contrário.
    """
    cpf = "10920930455"  # CPF sintético, não corresponde a pessoa real
    pagamento_em = datetime(2026, 8, 5, 4, 46, tzinfo=timezone.utc)
    _paid(db, cpf, pagamento_em, plan_name="Pro", plan_frequency="monthly")

    cancelamento_em = datetime(2026, 8, 5, 18, 19, tzinfo=timezone.utc)  # ~13h33 depois
    fields = {
        "event_type": "subscription_canceled",
        "customer_cpf": cpf,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(db, fields, reference_time=cancelamento_em)
    assert par is None


def test_cancelamento_chegando_sem_pagamento_nao_forma_par_com_outro_cancelamento(db):
    """Guarda de regressão: se `procurado` for trocado por engano para
    ['subscription_canceled'] (em vez de PAID_LIKE_EVENTS) no ramo reverso, esse
    teste falha — só existe um OUTRO evento subscription_canceled no banco (sem
    nenhum evento pago), e a função não pode confundir esse cancelamento com o
    par que está procurando."""
    cpf = "777"
    outro_cancelamento_em = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    _cancel(db, cpf, outro_cancelamento_em, plan_name="Pro", plan_frequency="monthly")

    fields = {
        "event_type": "subscription_canceled",
        "customer_cpf": cpf,
        "plan_name": "Essencial",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        db, fields, reference_time=outro_cancelamento_em + timedelta(minutes=8)
    )
    assert par is None


# --- backfill de subscription_id (Parte B.5 — reconciliação futura do import) ---


def _payload(order_id, cpf, email, subscription_id=None):
    return {
        "order": {
            "order_id": order_id,
            "webhook_event_type": "order_approved",
            "Customer": {"email": email, "full_name": "Cliente", "CPF": cpf},
            "Commissions": {"charge_amount": 6700, "kiwify_fee": 650, "my_commission": 6050},
            "Subscription": {
                "id": subscription_id,
                "status": "active",
                "plan": {"name": "Pro", "frequency": "monthly"},
                "customer_access": {"has_access": True, "access_until": "2030-01-01"},
            },
        }
    }


def test_subscription_id_real_adota_historico_orfao_do_mesmo_cpf(db):
    """Caso Karem: histórico importado sem subscription_id (cai por CPF). Quando
    a primeira renovação REAL chega com subscription_id da Kiwify, esse
    subscription_id deve "adotar" o evento importado antigo — senão ela vira
    2 assinantes a partir daí (um preso no CPF, outro no subscription_id novo)."""
    cpf = "99988877766"
    email = "karem@example.com"
    importado = SubscriptionEvent(
        event_type="import_subscription_active", customer_cpf=cpf,
        customer_email=email, subscription_id=None, has_access=True,
        raw_payload={}, dedupe_key="import:assinatura:1",
        received_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )
    db.add(importado)
    db.commit()
    importado_id = importado.id

    resultado = record_subscription_event(db, _payload("real-order-1", cpf, email, subscription_id="sub-real-123"), "order_approved")
    assert resultado is not None

    db.expire_all()
    orfao = db.query(SubscriptionEvent).filter(SubscriptionEvent.id == importado_id).first()
    assert orfao.subscription_id == "sub-real-123"


def test_backfill_nao_afeta_cpf_diferente(db):
    """Garante que o backfill é escopado por CPF — não vaza pra outras pessoas."""
    importado_outro = SubscriptionEvent(
        event_type="import_subscription_active", customer_cpf="00000000000",
        customer_email="outra@example.com", subscription_id=None, has_access=True,
        raw_payload={}, dedupe_key="import:assinatura:2",
        received_at=datetime(2026, 4, 14, tzinfo=timezone.utc),
    )
    db.add(importado_outro)
    db.commit()
    outro_id = importado_outro.id

    resultado = record_subscription_event(db, _payload("real-order-2", "11122233344", "novo@example.com", subscription_id="sub-real-999"), "order_approved")
    assert resultado is not None

    db.expire_all()
    intocado = db.query(SubscriptionEvent).filter(SubscriptionEvent.id == outro_id).first()
    assert intocado.subscription_id is None


def test_array_com_cobranca_desconhecida_alerta_e_nao_insere(caplog):
    """Rodada 6 item 1: o array não insere cobrança — só denuncia webhook perdido."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.services.subscription_event_recorder import alertar_cobrancas_desconhecidas

    ev = SimpleNamespace(
        subscription_id="sub-1",
        customer_cpf=None,
        customer_email="a@b.com",
        charges_completed=[
            {"order_id": "conhecida", "status": "paid", "amount": 100},
            {"order_id": "fantasma", "status": "paid", "amount": 999},
        ],
        raw_payload={},
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        ("conhecida", "REF1"),
    ]
    with caplog.at_level(logging.WARNING):
        desconhecidas = alertar_cobrancas_desconhecidas(db, ev)

    assert desconhecidas == ["fantasma"]
    assert "possível webhook perdido" in caplog.text
    db.add.assert_not_called()


def test_falha_no_alerta_nao_impede_registro_do_evento(db, monkeypatch):
    """Fix de review: alertar_cobrancas_desconhecidas é só uma verificação e
    nunca pode derrubar o registro do evento. Antes do fix, uma exceção aqui
    caía no `except Exception` externo (que não faz rollback nem re-flush) e
    fazia record_subscription_event retornar None — mesmo com a linha já
    persistida — o que deixava user_id NULL pra sempre (link/backfill pulados)."""
    import app.services.subscription_event_recorder as recorder_module

    def _explode(db, ev):
        raise RuntimeError("SELECT transitório falhou")

    monkeypatch.setattr(recorder_module, "alertar_cobrancas_desconhecidas", _explode)

    resultado = record_subscription_event(
        db, _payload("order-alerta-explode", "12345678900", "explode@example.com"), "order_approved"
    )

    assert resultado is not None
    assert resultado.order_id == "order-alerta-explode"
