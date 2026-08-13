"""Rodada 7, Task 14b: assinante sem user_id (import Kiwify sem conta
criada) não conta como "sem acesso 10d+" na lista — mesma regra que
_base_ativa() já aplica no card (platform_usage_service.py), pra número
do card e da lista baterem sempre.

Achado do script de validação da Task 14 (scripts/validar_rodada7.py)
rodando contra hml: card contava 17, lista contava 26 — 9 assinantes sem
user_id (import histórico da Kiwify, nunca criaram conta na plataforma)
apareciam na lista mas não no card."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _mock_db_for_list_clients():
    """Mesmo padrão de tests/unit/test_admin_metrics_service.py: DB
    genérico pra list_clients — toda query encadeada (filter/order_by)
    retorna vazio/None, só os eventos passados via monkeypatch importam."""
    db = MagicMock()

    def query_side_effect(model):
        q = MagicMock()
        q.filter.return_value = q
        q.filter_by.return_value = q
        q.order_by.return_value = q
        q.first.return_value = None
        q.all.return_value = []
        q.scalar.return_value = None
        return q

    db.query.side_effect = query_side_effect
    return db


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        subscription_id="sub-1",
        customer_email="importado@example.com",
        customer_name="Importado Sem Conta",
        customer_cpf=None,
        customer_phone=None,
        user_id=None,  # sem conta na plataforma
        received_at=datetime(2026, 1, 10, tzinfo=timezone.utc),
        subscription_start=None,
        next_payment=None,
        access_until=None,
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        card_rejection_reason=None,
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_no_login_10d_exclui_assinante_sem_user_id():
    svc = AdminMetricsService(_mock_db_for_list_clients())
    sem_conta = _ev()
    svc.active_subscribers = lambda as_of=None: [sem_conta]
    svc._all_events = lambda: [sem_conta]
    svc._semaphore = lambda uid: "red"

    linhas = svc.list_clients({"no_login_10d": True})

    assert linhas == []


def test_no_login_10d_mantem_assinante_com_user_id_sem_login_ha_mais_de_10_dias():
    """Regressão: o guard novo não pode excluir quem TEM user_id — só quem
    não tem. Assinante com conta na plataforma e sem login (last_login=None,
    caso mais comum) continua aparecendo na lista de sem acesso 10d+."""
    # customer_email=None: com o DB mockado genérico, a busca de User por id
    # (mock) não acha ninguém e list_clients() cai no fallback de busca por
    # e-mail — que também não acha nada e sobrescreveria `uid` de volta pra
    # None (comportamento pré-existente, fora do escopo deste fix). Sem
    # e-mail, esse fallback não dispara e `uid` continua o `ev.user_id` que
    # o teste quer exercitar.
    svc = AdminMetricsService(_mock_db_for_list_clients())
    com_conta = _ev(user_id=42, customer_email=None)
    svc.active_subscribers = lambda as_of=None: [com_conta]
    svc._all_events = lambda: [com_conta]
    svc._semaphore = lambda uid: "red"

    linhas = svc.list_clients({"no_login_10d": True})

    assert len(linhas) == 1
    assert linhas[0]["user_id"] == 42
