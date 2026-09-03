from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from app.repositories.subscription_repository import SubscriptionRepository
from app.services.payment_provider_service import check_active_subscription as provider_check, PaymentProviderError
import logging

logger = logging.getLogger(__name__)

# Sentinela: distingue "caller não informou janela de acesso" de "informou None"
UNSET = object()

# Status que representam assinatura cancelada (nossos + dos providers)
CANCELED_STATUSES = {"cancelada", "cancelado", "canceled", "cancelled"}


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def subscription_is_canceled(subscription) -> bool:
    """True se o provider já marcou a assinatura como cancelada/reembolsada."""
    if subscription is None:
        return False
    statuses = {
        (subscription.assinatura_status or "").strip().lower(),
        (subscription.provider_subscription_status or "").strip().lower(),
        (subscription.provider_status or "").strip().lower(),
    }
    return bool(statuses & CANCELED_STATUSES)


def subscription_has_access(subscription) -> bool:
    """
    Acesso efetivo do usuário.

    Assinatura cancelada continua com acesso até o fim do período já pago
    (Kiwify manda `customer_access.access_until`). Depois dessa data o acesso
    cai sozinho, sem depender de webhook novo — o cancelamento é o último
    evento que a Kiwify envia.
    """
    if subscription is None or not subscription.is_active:
        return False

    if not subscription_is_canceled(subscription):
        return True

    access_until = _as_utc(
        subscription.assinatura_vence_em
        or subscription.expires_at
        or subscription.provider_due_date
        or subscription.cakto_due_date
    )
    if access_until is None:
        return True  # cancelada sem data conhecida — respeita o is_active do webhook
    return access_until >= datetime.now(timezone.utc)


class SubscriptionService:
    def __init__(self, repo: SubscriptionRepository):
        self.repo = repo

    def set_active(
        self,
        user_id: int,
        plan: str,
        is_active: bool,
        cakto_customer_id: Optional[str] = None,
        cakto_transaction_id: Optional[str] = None,
        expires_at: Optional[datetime] = None,
        cakto_status: Optional[str] = None,
        cakto_offer_name: Optional[str] = None,
        cakto_due_date: Optional[datetime] = None,
        cakto_subscription_status: Optional[str] = None,
        cakto_payment_status: Optional[str] = None,
        cakto_payment_method: Optional[str] = None,
        # Generic provider fields
        provider: Optional[str] = None,
        provider_customer_id: Optional[str] = None,
        provider_transaction_id: Optional[str] = None,
        provider_status: Optional[str] = None,
        provider_offer_name: Optional[str] = None,
        provider_due_date: Optional[datetime] = None,
        provider_subscription_status: Optional[str] = None,
        provider_payment_status: Optional[str] = None,
        provider_payment_method: Optional[str] = None,
        provider_order_id: Optional[str] = None,
        # Plan tiers
        plano_periodo: Optional[str] = None,
        assinatura_status: Optional[str] = None,
        assinatura_vence_em: Optional[datetime] = None,
        # Janela de acesso pós-cancelamento decidida pelo caller (webhook).
        # datetime → mantém acesso até lá; None → corta acesso agora;
        # não informado → regra legada (cakto_due_date).
        keep_access_until: Any = UNSET,
    ):
        """Atualiza ou cria subscription com dados do provider (Cakto/Kiwify)."""
        incoming_txn = (provider_transaction_id or cakto_transaction_id or "").strip() or None

        # Guard anti-cancelamento-de-assinatura-antiga: webhooks chegam FORA DE ORDEM
        # (ex.: "Assinatura cancelada" da sub velha depois da "Compra aprovada" da nova).
        # Como há uma linha por usuário (last-write-wins), um cancelamento de OUTRA transação
        # derrubava quem acabou de comprar. Só desativamos se o cancelamento for da transação
        # vigente; cancelamento de transação diferente (sub antiga) é ignorado.
        if not is_active:
            current = self.repo.get_by_user_id(user_id)
            # (10.2) Cancelamento da compra PENDENTE: só some a pendência —
            # a principal (tier maior, ainda vigente) não é tocada.
            if (
                current is not None
                and incoming_txn
                and getattr(current, "pending_provider_transaction_id", None) == incoming_txn
            ):
                logger.info(
                    "Cancelamento da assinatura PENDENTE p/ user %s (txn=%s): limpa pending_*, principal intacta.",
                    user_id, incoming_txn,
                )
                self._limpar_pendente(current)
                self.repo.db.commit()
                self.repo.db.refresh(current)
                return current
            current_txn = (current.provider_transaction_id or current.cakto_transaction_id) if current else None
            if current and current.is_active and incoming_txn and current_txn and incoming_txn != current_txn:
                logger.info(
                    "Cancelamento ignorado p/ user %s: assinatura ativa (txn=%s) e cancelamento de OUTRA txn (%s).",
                    user_id, current_txn, incoming_txn,
                )
                return current

        normalized_offer_name = None
        if isinstance(cakto_offer_name, str):
            stripped_offer = cakto_offer_name.strip()
            normalized_offer_name = stripped_offer or None
        
        from app.core.plans import normalize_plan

        normalized_plan = None
        if isinstance(plan, str):
            stripped_plan = plan.strip()
            normalized_plan = stripped_plan or None

        # Plano canônico: essencial|pro|max (legado free/marketdash normalizado)
        plan_value = normalize_plan(normalized_plan or "essencial")
        # Cakto legado às vezes passava nome da oferta como "plan" — se não for tier conhecido,
        # normalize_plan já cai em essencial; offer name fica em cakto_offer_name.

        normalized_due_date = cakto_due_date  # Due date pode ser None e deve refletir diretamente em expires_at

        normalized_status = None
        if isinstance(cakto_status, str):
            stripped_status = cakto_status.strip()
            if stripped_status:
                normalized_status = stripped_status.lower()
                cakto_status = stripped_status
            else:
                cakto_status = None
        
        normalized_subscription_status = None
        if isinstance(cakto_subscription_status, str):
            stripped_sub_status = cakto_subscription_status.strip()
            normalized_subscription_status = stripped_sub_status or None
        else:
            normalized_subscription_status = cakto_subscription_status

        normalized_payment_status = None
        if isinstance(cakto_payment_status, str):
            stripped_payment_status = cakto_payment_status.strip()
            normalized_payment_status = stripped_payment_status or None
        else:
            normalized_payment_status = cakto_payment_status

        normalized_payment_method = None
        if isinstance(cakto_payment_method, str):
            stripped_payment_method = cakto_payment_method.strip()
            normalized_payment_method = stripped_payment_method or None
        else:
            normalized_payment_method = cakto_payment_method

        if normalized_due_date is not None and normalized_due_date.tzinfo is None:
            normalized_due_date = normalized_due_date.replace(tzinfo=timezone.utc)

        now_utc = datetime.now(timezone.utc)
        is_active_value = is_active  # Webhook é a fonte de verdade

        # Se NÃO foi pedida ativação explícita, a janela de acesso decide o status.
        # Quando is_active=True (ex: webhook de renovação), respeitar a decisão do caller.
        if not is_active:
            if keep_access_until is not UNSET:
                # Caller informou explicitamente a janela (cancelamento com acesso
                # até o fim do período pago vs. reembolso/chargeback, que corta na hora)
                grace_until = _as_utc(keep_access_until)
                is_active_value = bool(grace_until and grace_until >= now_utc)
            elif normalized_due_date is not None:
                is_active_value = normalized_due_date >= now_utc

        vence = assinatura_vence_em or normalized_due_date or expires_at
        status_assinatura = assinatura_status
        if status_assinatura is None:
            status_assinatura = "ativa" if is_active_value else "cancelada"

        # (10.2) Guard de ativação: comprar tier MENOR com tier maior ainda
        # vigente NÃO pode rebaixar na hora (uma linha por usuário = last-write-
        # wins derrubava acesso pago). A compra fica pendente e é promovida
        # quando a principal perder o acesso (_promover_pendente_se_devido).
        if is_active:
            from app.core.plans import PLAN_RANK

            current = self.repo.get_by_user_id(user_id)
            if current is not None:
                current_txn = current.provider_transaction_id or current.cakto_transaction_id
                rank_atual = PLAN_RANK.get(normalize_plan(current.plan), 0)
                rank_novo = PLAN_RANK.get(plan_value, 0)
                if (
                    rank_novo < rank_atual
                    and incoming_txn
                    and current_txn
                    and incoming_txn != current_txn
                    and subscription_has_access(current)
                ):
                    logger.info(
                        "Ativação de tier menor p/ user %s (%s < %s, txn=%s): "
                        "principal vigente mantida, compra gravada como pendente.",
                        user_id, plan_value, normalize_plan(current.plan), incoming_txn,
                    )
                    current.pending_plan = plan_value
                    current.pending_periodo = plano_periodo
                    # Mesma precedência do `vence` da principal, com o due date
                    # do provider como reforço (Kiwify manda os dois).
                    current.pending_vence_em = vence or provider_due_date
                    current.pending_provider_transaction_id = incoming_txn
                    self.repo.db.commit()
                    self.repo.db.refresh(current)
                    return current

        subscription = self.repo.upsert(
            user_id=user_id,
            plan=plan_value,
            is_active=is_active_value,
            cakto_customer_id=cakto_customer_id,
            cakto_transaction_id=cakto_transaction_id,
            expires_at=normalized_due_date if normalized_due_date is not None else expires_at,
            cakto_status=cakto_status,
            cakto_offer_name=normalized_offer_name,
            cakto_due_date=normalized_due_date,
            cakto_subscription_status=normalized_subscription_status,
            cakto_payment_status=normalized_payment_status,
            cakto_payment_method=normalized_payment_method,
            # Generic provider fields
            provider=provider,
            provider_customer_id=provider_customer_id,
            provider_transaction_id=provider_transaction_id,
            provider_status=provider_status,
            provider_offer_name=provider_offer_name or normalized_offer_name,
            provider_due_date=provider_due_date or normalized_due_date,
            provider_subscription_status=provider_subscription_status,
            provider_payment_status=provider_payment_status,
            provider_payment_method=provider_payment_method,
            provider_order_id=provider_order_id,
            plano_periodo=plano_periodo,
            assinatura_status=status_assinatura,
            assinatura_vence_em=vence,
        )

        # (10.2) A txn ativada era justamente a pendente (ex.: principal já sem
        # acesso quando a ativação chegou) — a pendência deixa de existir.
        if (
            is_active
            and incoming_txn
            and getattr(subscription, "pending_provider_transaction_id", None) == incoming_txn
        ):
            self._limpar_pendente(subscription)
            self.repo.db.commit()
            self.repo.db.refresh(subscription)

        return subscription

    @staticmethod
    def _limpar_pendente(subscription) -> None:
        subscription.pending_plan = None
        subscription.pending_periodo = None
        subscription.pending_vence_em = None
        subscription.pending_provider_transaction_id = None

    def _promover_pendente_se_devido(self, subscription) -> bool:
        """(10.2) Promove a compra pendente quando a principal perde o acesso.

        Muda o objeto em memória e retorna True se algo mudou — o CALLER
        comita. A promoção acontece nos caminhos de leitura/checagem de acesso
        (get_effective_subscription / check_and_update_subscription), sem
        depender de webhook novo.
        """
        if subscription is None or not getattr(subscription, "pending_plan", None):
            return False
        if subscription_has_access(subscription):
            return False

        pendente_vence = _as_utc(subscription.pending_vence_em)
        if pendente_vence is None or pendente_vence < datetime.now(timezone.utc):
            # A pendente também já venceu — só limpa, sem ressuscitar acesso.
            self._limpar_pendente(subscription)
            return True

        logger.info(
            "Promovendo assinatura pendente p/ user %s: %s (txn=%s) vira a principal.",
            subscription.user_id if hasattr(subscription, "user_id") else "?",
            subscription.pending_plan,
            subscription.pending_provider_transaction_id,
        )
        subscription.plan = subscription.pending_plan
        subscription.plano_periodo = subscription.pending_periodo
        subscription.assinatura_vence_em = subscription.pending_vence_em
        subscription.expires_at = subscription.pending_vence_em
        subscription.provider_due_date = subscription.pending_vence_em
        subscription.provider_transaction_id = subscription.pending_provider_transaction_id
        subscription.is_active = True
        subscription.assinatura_status = "ativa"
        # Os status de cancelamento eram da assinatura ANTIGA — mantê-los
        # deixaria a promovida "cancelada" aos olhos de subscription_has_access.
        subscription.provider_subscription_status = None
        subscription.provider_status = None
        # O offer_name também era da antiga, e NÃO é decorativo: a revalidação
        # de 30 dias faz `plan = provider_offer_name or cakto_offer_name`
        # (check_and_update_subscription). Deixá-lo aqui reverteria o plano
        # promovido para o tier que expirou, na primeira revalidação — o
        # downgrade que a 10.2 existe para impedir, com um mês de atraso.
        subscription.provider_offer_name = None
        subscription.cakto_offer_name = None
        self._limpar_pendente(subscription)
        return True

    def get_effective_subscription(self, user_id: int):
        """Assinatura efetiva do usuário, já aplicando a promoção pendente."""
        subscription = self.repo.get_by_user_id(user_id)
        if subscription is not None and self._promover_pendente_se_devido(subscription):
            self.repo.db.commit()
            self.repo.db.refresh(subscription)
        return subscription

    def needs_validation(self, user_id: int) -> bool:
        """Verifica se precisa validar assinatura (passou mais de 30 dias)."""
        subscription = self.repo.get_by_user_id(user_id)
        if not subscription:
            return True  # Se não tem subscription, precisa validar
        
        if not subscription.last_validation_at:
            return True  # Nunca validou
        
        # Verificar se passou mais de 30 dias
        days_since_validation = (datetime.now(timezone.utc) - subscription.last_validation_at).days
        return days_since_validation >= 30

    def check_and_update_subscription(self, user_id: int, user_email: str) -> bool:
        """Valida assinatura com o provider ativo e atualiza no banco. Retorna True se ativa."""
        try:
            has_access, reason = provider_check(user_email)

            subscription = self.repo.get_by_user_id(user_id)
            if not subscription:
                subscription = self.repo.upsert(
                    user_id=user_id,
                    plan="free",
                    is_active=False,
                )

            now_utc = datetime.now(timezone.utc)

            # Usar provider_due_date se disponível, fallback para cakto_due_date
            due_date = subscription.provider_due_date or subscription.cakto_due_date
            if due_date and due_date.tzinfo is None:
                due_date = due_date.replace(tzinfo=timezone.utc)

            within_paid_period = bool(due_date and due_date >= now_utc)
            if subscription_is_canceled(subscription):
                # Cancelada: só mantém acesso até o fim do período já pago, e nunca
                # ressuscita quem já teve o acesso cortado (reembolso/chargeback).
                subscription.is_active = bool(subscription.is_active and within_paid_period)
            else:
                subscription.is_active = within_paid_period
            subscription.last_validation_at = now_utc

            offer_name = subscription.provider_offer_name or subscription.cakto_offer_name
            if offer_name:
                subscription.plan = offer_name
            elif not subscription.plan:
                subscription.plan = "free"

            effective_due = subscription.provider_due_date or subscription.cakto_due_date
            if effective_due:
                subscription.expires_at = effective_due
            elif not subscription.is_active:
                subscription.expires_at = None

            # (10.2) Principal acabou de perder o acesso e existe compra
            # pendente vigente → ela assume aqui, sem esperar webhook.
            self._promover_pendente_se_devido(subscription)

            self.repo.db.commit()
            self.repo.db.refresh(subscription)

            # Retorna o acesso efetivo (o mesmo que os demais gates leem do banco),
            # não o veredito cru do provider — senão cancelado-com-acesso tomaria 403.
            effective_access = subscription_has_access(subscription)
            logger.info(
                "Subscription validated for user %s: provider=%s effective=%s",
                user_id, has_access, effective_access,
            )
            return effective_access

        except PaymentProviderError as e:
            logger.error(f"Error validating subscription for user {user_id}: {str(e)}")
            return False

    def get_subscription_status(self, user_id: int) -> Dict[str, Any]:
        """Retorna status completo da assinatura do usuário."""
        # Leitura de acesso já aplica a promoção da pendente (10.2)
        subscription = self.get_effective_subscription(user_id)
        
        from app.core.plans import normalize_plan

        if not subscription:
            return {
                "has_subscription": False,
                "is_active": False,
                "plan": "essencial",
                "plano": "essencial",
                "plano_periodo": None,
                "assinatura_status": "cancelada",
                "assinatura_vence_em": None,
                "needs_validation": True,
                "expires_at": None,
            }
        
        needs_validation = self.needs_validation(user_id)
        plan = normalize_plan(subscription.plan)
        has_access = subscription_has_access(subscription)

        return {
            "has_subscription": True,
            "is_active": has_access,
            "plan": plan,
            "plano": plan,
            "plano_periodo": subscription.plano_periodo,
            "assinatura_status": subscription.assinatura_status
            or ("ativa" if subscription.is_active else "cancelada"),
            "assinatura_vence_em": (
                (subscription.assinatura_vence_em or subscription.expires_at).isoformat()
                if (subscription.assinatura_vence_em or subscription.expires_at)
                else None
            ),
            "needs_validation": needs_validation,
            "last_validation_at": subscription.last_validation_at.isoformat() if subscription.last_validation_at else None,
            "expires_at": subscription.expires_at.isoformat() if subscription.expires_at else None,
            # Provider-agnostic fields
            "provider": subscription.provider,
            "provider_customer_id": subscription.provider_customer_id,
            "provider_status": subscription.provider_status,
            "provider_offer_name": subscription.provider_offer_name,
            "provider_due_date": subscription.provider_due_date.isoformat() if subscription.provider_due_date else None,
            "provider_subscription_status": subscription.provider_subscription_status,
            "provider_payment_status": subscription.provider_payment_status,
            "provider_payment_method": subscription.provider_payment_method,
            "provider_order_id": subscription.provider_order_id,
            # Legacy Cakto fields (backward compat)
            "cakto_customer_id": subscription.cakto_customer_id,
            "cakto_status": subscription.cakto_status,
            "cakto_offer_name": subscription.cakto_offer_name,
            "cakto_due_date": subscription.cakto_due_date.isoformat() if subscription.cakto_due_date else None,
            "cakto_next_payment_date": subscription.cakto_due_date.isoformat() if subscription.cakto_due_date else None,
            "cakto_subscription_status": subscription.cakto_subscription_status,
            "cakto_payment_status": subscription.cakto_payment_status,
            "cakto_payment_method": subscription.cakto_payment_method,
        }
    
    def cancel_subscription(self, user_id: int) -> bool:
        """
        Cancela a assinatura do usuário.
        
        Desativa a assinatura no nosso sistema. O cancelamento real na Cakto
        deve ser feito pelo usuário na plataforma Cakto. Quando o cancelamento
        for processado pela Cakto, o webhook atualizará automaticamente.
        
        Returns:
            True se a assinatura foi cancelada, False se não havia assinatura ativa
        """
        subscription = self.repo.get_by_user_id(user_id)
        
        if not subscription:
            logger.info(f"Tentativa de cancelar assinatura para usuário {user_id} sem subscription")
            return False
        
        if not subscription.is_active:
            logger.info(f"Assinatura do usuário {user_id} já estava inativa")
            return False

        # Cancelar NÃO corta o acesso na hora: vale até o fim do período já pago
        # (mesma regra do webhook de cancelamento). subscription_has_access corta
        # sozinho quando a data passar.
        subscription.plan = "essencial"
        subscription.assinatura_status = "cancelada"
        subscription.is_active = subscription_has_access(subscription)

        # Fazer commit
        self.repo.db.commit()
        self.repo.db.refresh(subscription)
        
        logger.info(f"Assinatura cancelada para usuário {user_id}")
        return True
