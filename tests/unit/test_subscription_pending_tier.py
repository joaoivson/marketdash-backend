"""
Compra pendente de tier menor (spec 10.2).

Bug real: `subscriptions` tem uma linha por usuário (user_id UNIQUE,
last-write-wins). Comprar Pro com MAX ainda vigente sobrescrevia a linha e
REBAIXAVA na hora — perda de acesso pago. Agora a ativação de tier menor fica
nas colunas pending_* e só é promovida quando a principal perder o acesso.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.subscription_service import (
    SubscriptionService,
    subscription_has_access,
)

NOW = datetime.now(timezone.utc)
FUTURE_MAX = NOW + timedelta(days=20)
FUTURE_PRO = NOW + timedelta(days=45)
PAST = NOW - timedelta(days=2)


def _sub(**kwargs):
    base = dict(
        user_id=1,
        plan="max",
        is_active=True,
        plano_periodo="mensal",
        assinatura_status="ativa",
        assinatura_vence_em=FUTURE_MAX,
        expires_at=FUTURE_MAX,
        provider="kiwify",
        provider_transaction_id="txn-max",
        cakto_transaction_id=None,
        provider_status=None,
        provider_subscription_status=None,
        provider_offer_name=None,
        provider_due_date=FUTURE_MAX,
        cakto_due_date=None,
        cakto_offer_name=None,
        last_validation_at=None,
        pending_plan=None,
        pending_periodo=None,
        pending_vence_em=None,
        pending_provider_transaction_id=None,
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class _FakeDb:
    def commit(self):
        pass

    def refresh(self, _obj):
        pass


class _FakeRepo:
    """Upsert espelha kwargs em SimpleNamespace (mesmo padrão dos testes de
    cancelamento). `saved is None` = a principal NÃO foi sobrescrita."""

    def __init__(self, current=None):
        self.current = current
        self.saved = None
        self.db = _FakeDb()

    def get_by_user_id(self, user_id):
        return self.current

    def upsert(self, **kwargs):
        self.saved = SimpleNamespace(**kwargs)
        return self.saved


class _RepoUpsertInPlace(_FakeRepo):
    """Upsert aplica os kwargs NA linha existente — é o que o repo real faz
    (uma linha por usuário)."""

    def upsert(self, **kwargs):
        self.saved = kwargs
        for key, value in kwargs.items():
            setattr(self.current, key, value)
        return self.current


def _ativar_pro(service, **overrides):
    kwargs = dict(
        user_id=1,
        plan="pro",
        is_active=True,
        provider="kiwify",
        provider_transaction_id="txn-pro",
        plano_periodo="mensal",
        assinatura_status="ativa",
        assinatura_vence_em=FUTURE_PRO,
        provider_due_date=FUTURE_PRO,
        expires_at=FUTURE_PRO,
    )
    kwargs.update(overrides)
    return service.set_active(**kwargs)


# --- (a) ativação de tier menor com maior vigente --------------------------------


def test_ativar_tier_menor_com_maior_vigente_nao_rebaixa_e_grava_pendente():
    current = _sub()
    repo = _FakeRepo(current)
    result = _ativar_pro(SubscriptionService(repo))

    assert repo.saved is None  # principal intacta — upsert nem foi chamado
    assert result is current
    assert current.plan == "max"
    assert current.is_active is True
    assert current.pending_plan == "pro"
    assert current.pending_periodo == "mensal"
    assert current.pending_vence_em == FUTURE_PRO
    assert current.pending_provider_transaction_id == "txn-pro"


def test_ativar_tier_menor_com_maior_cancelada_mas_com_acesso_tambem_pendura():
    # Cancelada ≠ sem acesso: vale até o fim do período pago
    current = _sub(assinatura_status="cancelada", assinatura_vence_em=FUTURE_MAX)
    repo = _FakeRepo(current)
    _ativar_pro(SubscriptionService(repo))

    assert repo.saved is None
    assert current.plan == "max"
    assert current.pending_plan == "pro"


def test_ativar_tier_menor_com_maior_sem_acesso_sobrescreve_normal():
    current = _sub(
        assinatura_status="cancelada",
        assinatura_vence_em=PAST,
        expires_at=PAST,
        provider_due_date=PAST,
    )
    repo = _FakeRepo(current)
    _ativar_pro(SubscriptionService(repo))

    assert repo.saved is not None
    assert repo.saved.plan == "pro"


def test_ativar_tier_menor_da_mesma_txn_sobrescreve():
    # Guard exige txn DIFERENTE — mudança de plano na mesma assinatura aplica direto
    current = _sub(provider_transaction_id="txn-pro")
    repo = _FakeRepo(current)
    _ativar_pro(SubscriptionService(repo))

    assert repo.saved is not None
    assert repo.saved.plan == "pro"


# --- (b) promoção quando a principal perde o acesso ------------------------------


def _sub_expirada_com_pendente(**overrides):
    kwargs = dict(
        assinatura_status="cancelada",
        provider_subscription_status="canceled",
        assinatura_vence_em=PAST,
        expires_at=PAST,
        provider_due_date=PAST,
        pending_plan="pro",
        pending_periodo="mensal",
        pending_vence_em=FUTURE_PRO,
        pending_provider_transaction_id="txn-pro",
    )
    kwargs.update(overrides)
    return _sub(**kwargs)


def test_principal_expirada_promove_pendente_na_leitura():
    sub = _sub_expirada_com_pendente()
    service = SubscriptionService(_FakeRepo(sub))

    result = service.get_effective_subscription(1)

    assert result is sub
    assert sub.plan == "pro"
    assert sub.plano_periodo == "mensal"
    assert sub.is_active is True
    assert sub.assinatura_status == "ativa"
    assert sub.assinatura_vence_em == FUTURE_PRO
    assert sub.expires_at == FUTURE_PRO
    assert sub.provider_transaction_id == "txn-pro"
    assert sub.pending_plan is None
    assert sub.pending_provider_transaction_id is None


def test_principal_com_acesso_nao_promove():
    sub = _sub(
        pending_plan="pro",
        pending_periodo="mensal",
        pending_vence_em=FUTURE_PRO,
        pending_provider_transaction_id="txn-pro",
    )
    service = SubscriptionService(_FakeRepo(sub))
    service.get_effective_subscription(1)

    assert sub.plan == "max"
    assert sub.pending_plan == "pro"


def test_pendente_tambem_vencida_so_limpa_sem_ressuscitar_acesso():
    sub = _sub_expirada_com_pendente(pending_vence_em=PAST)
    service = SubscriptionService(_FakeRepo(sub))
    service.get_effective_subscription(1)

    assert sub.plan == "max"
    assert subscription_has_access(sub) is False
    assert sub.pending_plan is None


def test_check_and_update_promove_pendente_sem_webhook_novo(monkeypatch):
    # A promoção não depende de webhook: o check periódico já promove
    monkeypatch.setattr(
        "app.services.subscription_service.provider_check",
        lambda email: (False, "expirada no provider"),
    )
    sub = _sub_expirada_com_pendente(is_active=True)
    service = SubscriptionService(_FakeRepo(sub))

    assert service.check_and_update_subscription(1, "a@b.c") is True
    assert sub.plan == "pro"
    assert sub.is_active is True
    assert sub.pending_plan is None


# --- (c) cancelamento da pendente ------------------------------------------------


def test_cancelamento_da_pendente_limpa_pending_e_nao_toca_na_principal():
    current = _sub(
        pending_plan="pro",
        pending_periodo="mensal",
        pending_vence_em=FUTURE_PRO,
        pending_provider_transaction_id="txn-pro",
    )
    repo = _FakeRepo(current)
    result = SubscriptionService(repo).set_active(
        user_id=1,
        plan="essencial",
        is_active=False,
        provider="kiwify",
        provider_transaction_id="txn-pro",
        keep_access_until=None,
    )

    assert repo.saved is None
    assert result is current
    assert current.plan == "max"
    assert current.is_active is True
    assert current.pending_plan is None
    assert current.pending_vence_em is None
    assert current.pending_provider_transaction_id is None


def test_cancelamento_de_terceira_txn_continua_ignorado_e_preserva_pendente():
    # Guard existente (webhook fora de ordem) segue valendo
    current = _sub(
        pending_plan="pro",
        pending_provider_transaction_id="txn-pro",
    )
    repo = _FakeRepo(current)
    SubscriptionService(repo).set_active(
        user_id=1,
        plan="essencial",
        is_active=False,
        provider="kiwify",
        provider_transaction_id="txn-antiga",
        keep_access_until=None,
    )

    assert repo.saved is None
    assert current.is_active is True
    assert current.pending_plan == "pro"


# --- (d) tier maior ou igual sobrescreve normalmente -----------------------------


def test_ativar_tier_maior_sobrescreve_normalmente():
    current = _sub(plan="pro", provider_transaction_id="txn-pro")
    repo = _FakeRepo(current)
    SubscriptionService(repo).set_active(
        user_id=1,
        plan="max",
        is_active=True,
        provider="kiwify",
        provider_transaction_id="txn-max",
        plano_periodo="mensal",
        assinatura_status="ativa",
        assinatura_vence_em=FUTURE_MAX,
        provider_due_date=FUTURE_MAX,
        expires_at=FUTURE_MAX,
    )

    assert repo.saved is not None
    assert repo.saved.plan == "max"
    assert repo.saved.is_active is True


def test_ativar_mesmo_tier_sobrescreve_normalmente():
    current = _sub(plan="pro", provider_transaction_id="txn-pro-velha")
    repo = _FakeRepo(current)
    _ativar_pro(SubscriptionService(repo))

    assert repo.saved is not None
    assert repo.saved.plan == "pro"


def test_ativacao_da_propria_pendente_vira_principal_e_limpa_pending():
    # Principal já sem acesso quando a ativação da pendente chega
    current = _sub_expirada_com_pendente(is_active=False)
    repo = _RepoUpsertInPlace(current)
    _ativar_pro(SubscriptionService(repo))

    assert current.plan == "pro"
    assert current.pending_plan is None
    assert current.pending_provider_transaction_id is None
