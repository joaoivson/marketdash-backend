"""Rodada 6, item 3: upgrade vale em QUALQUER ordem.

Ana Ariel assinou Essencial 10/08 11:11, Pro 11:19 e só depois a Essencial foi
cancelada. A regra antiga só olhava "cancelou antes, assinou depois" — o par
nunca era detectado, e ela contava como nova assinatura E como churn.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.subscription_event_recorder import encontrar_par_de_plan_change

CPF = "12345678900"
BASE = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        customer_cpf=CPF,
        plan_name="Essencial",
        plan_frequency="monthly",
        received_at=BASE,
        is_plan_change=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _db_com(eventos):
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = eventos
    return db


def test_cancelamento_depois_da_nova_assinatura_forma_par():
    """Chega o CANCELAMENTO da Essencial; a Pro já entrou 8 minutos antes."""
    pro = _ev(id=2, event_type="order_approved", plan_name="Pro", received_at=BASE + timedelta(minutes=19))
    fields = {
        "event_type": "subscription_canceled",
        "customer_cpf": CPF,
        "plan_name": "Essencial",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([pro]), fields, reference_time=BASE + timedelta(minutes=20)
    )
    assert par is pro


def test_nova_assinatura_depois_do_cancelamento_continua_funcionando():
    """Ordem antiga: cancelou, depois reassinou plano diferente."""
    cancelamento = _ev(id=3, event_type="subscription_canceled", plan_name="Essencial")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(days=3)
    )
    assert par is cancelamento


def test_mesmo_plano_em_ate_um_dia_e_continuacao():
    cancelamento = _ev(id=4, event_type="subscription_canceled", plan_name="Pro")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(hours=2)
    )
    assert par is cancelamento


def test_mesmo_plano_depois_de_uma_semana_nao_e_par():
    cancelamento = _ev(id=5, event_type="subscription_canceled", plan_name="Pro")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(days=7)
    )
    assert par is None


def test_plano_diferente_depois_de_40_dias_nao_e_par():
    cancelamento = _ev(id=6, event_type="subscription_canceled", plan_name="Essencial")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(days=40)
    )
    assert par is None


def test_sem_cpf_nao_forma_par():
    fields = {"event_type": "order_approved", "customer_cpf": None, "plan_name": "Pro"}
    assert encontrar_par_de_plan_change(_db_com([]), fields) is None
