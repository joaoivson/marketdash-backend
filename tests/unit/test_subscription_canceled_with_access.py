"""
Cancelamento com acesso até o fim do período pago.

Bug real (31/07/2026): a Kiwify mandou `subscription_canceled` com
`customer_access.has_access: true` e `access_until: 31/08`, mas o webhook zerava
`is_active` na hora — o painel admin (que lê subscription_events) mostrava
"Cancelado c/ acesso" enquanto o app barrava o usuário no login.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.api.v1.routes.kiwify import (
    _extract_customer_access,
    _resolve_keep_access_until,
)
from app.services.subscription_service import (
    UNSET,
    SubscriptionService,
    subscription_has_access,
    subscription_is_canceled,
)

NOW = datetime.now(timezone.utc)
FUTURE = NOW + timedelta(days=31)
PAST = NOW - timedelta(days=2)


def _cancel_payload(has_access=True, access_until="2026-08-31T22:06:53.212Z"):
    """Payload real do webhook de cancelamento (recortado)."""
    access = {"active_period": True, "has_access": has_access}
    if access_until is not None:
        access["access_until"] = access_until
    return {
        "order": {
            "order_id": "ord-1",
            "webhook_event_type": "subscription_canceled",
            "Customer": {"email": "deivitrafael56@gmail.com"},
            "Subscription": {
                "start_date": "2026-07-31T22:05:24.955Z",
                "next_payment": "2026-08-31T22:06:53.212Z",
                "status": "canceled",
                "customer_access": access,
            },
        }
    }


# --- extração do customer_access -------------------------------------------------


def test_extract_customer_access_le_has_access_e_access_until():
    access = _extract_customer_access(_cancel_payload()["order"])
    assert access["has_access"] is True
    assert access["access_until"] == datetime(2026, 8, 31, 22, 6, 53, 212000, tzinfo=timezone.utc)


def test_extract_customer_access_sem_bloco_retorna_vazio():
    access = _extract_customer_access({"Subscription": {"status": "canceled"}})
    assert access == {"has_access": None, "access_until": None}


# --- política de corte de acesso -------------------------------------------------


def test_cancelamento_mantem_acesso_ate_access_until():
    access = _extract_customer_access(_cancel_payload()["order"])
    keep = _resolve_keep_access_until("subscription_canceled", access, fallback_due_date=None)
    assert keep == datetime(2026, 8, 31, 22, 6, 53, 212000, tzinfo=timezone.utc)


def test_cancelamento_sem_access_until_usa_next_payment():
    access = _extract_customer_access(_cancel_payload(access_until=None)["order"])
    keep = _resolve_keep_access_until("subscription_canceled", access, fallback_due_date=FUTURE)
    assert keep == FUTURE


def test_reembolso_corta_acesso_na_hora():
    access = _extract_customer_access(_cancel_payload()["order"])
    assert _resolve_keep_access_until("order_refunded", access, fallback_due_date=FUTURE) is None
    assert _resolve_keep_access_until("order_chargedback", access, fallback_due_date=FUTURE) is None


def test_has_access_false_corta_acesso_na_hora():
    access = _extract_customer_access(_cancel_payload(has_access=False)["order"])
    assert _resolve_keep_access_until("subscription_canceled", access, fallback_due_date=FUTURE) is None


# --- set_active ------------------------------------------------------------------


class _FakeRepo:
    """Repo em memória — o upsert real só faz espelhar kwargs na linha."""

    def __init__(self, current=None):
        self.current = current
        self.saved = None

    def get_by_user_id(self, user_id):
        return self.current

    def upsert(self, **kwargs):
        self.saved = SimpleNamespace(**kwargs)
        return self.saved


def _set_active(keep_access_until, **overrides):
    repo = _FakeRepo()
    service = SubscriptionService(repo)
    kwargs = dict(
        user_id=1,
        plan="essencial",
        is_active=False,
        provider="kiwify",
        provider_due_date=FUTURE,
        expires_at=FUTURE,
        assinatura_status="cancelada",
        assinatura_vence_em=FUTURE,
    )
    kwargs.update(overrides)
    if keep_access_until is not UNSET:
        kwargs["keep_access_until"] = keep_access_until
    service.set_active(**kwargs)
    return repo.saved


def test_set_active_cancelamento_com_janela_futura_mantem_ativo():
    saved = _set_active(FUTURE)
    assert saved.is_active is True
    assert saved.assinatura_status == "cancelada"


def test_set_active_cancelamento_com_janela_vencida_desativa():
    saved = _set_active(PAST)
    assert saved.is_active is False


def test_set_active_reembolso_desativa_mesmo_com_due_date_futura():
    saved = _set_active(None)
    assert saved.is_active is False


def test_set_active_sem_parametro_mantem_regra_legada_cakto():
    # Cakto não passa keep_access_until: quem decide é cakto_due_date
    saved = _set_active(UNSET, cakto_due_date=FUTURE)
    assert saved.is_active is True

    saved = _set_active(UNSET, cakto_due_date=PAST)
    assert saved.is_active is False


def test_set_active_ativacao_ignora_janela():
    saved = _set_active(UNSET, is_active=True, assinatura_status="ativa", cakto_due_date=PAST)
    assert saved.is_active is True


# --- acesso efetivo --------------------------------------------------------------


def _sub(**kwargs):
    base = dict(
        is_active=True,
        assinatura_status="ativa",
        provider_subscription_status=None,
        provider_status=None,
        assinatura_vence_em=None,
        expires_at=None,
        provider_due_date=None,
        cakto_due_date=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_has_access_assinatura_ativa():
    assert subscription_has_access(_sub()) is True


def test_has_access_cancelada_dentro_da_janela():
    sub = _sub(assinatura_status="cancelada", assinatura_vence_em=FUTURE)
    assert subscription_is_canceled(sub) is True
    assert subscription_has_access(sub) is True


def test_has_access_cancelada_com_janela_vencida():
    # Sem webhook novo depois do cancelamento, é a data que corta o acesso
    sub = _sub(assinatura_status="cancelada", assinatura_vence_em=PAST)
    assert subscription_has_access(sub) is False


def test_has_access_detecta_cancelamento_pelo_status_do_provider():
    sub = _sub(provider_subscription_status="canceled", provider_due_date=PAST)
    assert subscription_has_access(sub) is False


def test_has_access_janela_naive_tratada_como_utc():
    sub = _sub(assinatura_status="cancelada", assinatura_vence_em=FUTURE.replace(tzinfo=None))
    assert subscription_has_access(sub) is True


def test_has_access_is_active_false_nunca_libera():
    assert subscription_has_access(_sub(is_active=False, assinatura_vence_em=FUTURE)) is False


def test_has_access_sem_subscription():
    assert subscription_has_access(None) is False


# --- cancelamento pelo app (POST /subscription/cancel) ---------------------------


class _FakeDb:
    def commit(self):
        pass

    def refresh(self, _obj):
        pass


class _RepoComDb(_FakeRepo):
    def __init__(self, current):
        super().__init__(current)
        self.db = _FakeDb()


def test_cancelamento_no_app_mantem_acesso_ate_o_fim_do_periodo_pago():
    sub = _sub(expires_at=FUTURE)
    service = SubscriptionService(_RepoComDb(sub))

    assert service.cancel_subscription(1) is True
    assert sub.assinatura_status == "cancelada"
    assert sub.is_active is True


def test_cancelamento_no_app_sem_periodo_restante_corta_acesso():
    sub = _sub(expires_at=PAST)
    service = SubscriptionService(_RepoComDb(sub))

    assert service.cancel_subscription(1) is True
    assert sub.is_active is False
