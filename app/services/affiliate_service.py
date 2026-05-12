import logging
from decimal import Decimal
from typing import Optional

from sqlalchemy.orm import Session

from app.models.commission import Commission
from app.models.subscription import Subscription
from app.models.user import User
from app.repositories.commission_repository import CommissionRepository

logger = logging.getLogger(__name__)

DEFAULT_COMMISSION_RATE = Decimal("0.40")


class AffiliateService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = CommissionRepository(db)

    def create_commission_from_payment(
        self,
        referred_user: User,
        subscription: Optional[Subscription],
        amount: Optional[float | int | str | Decimal],
        cakto_transaction_id: Optional[str],
    ) -> Optional[Commission]:
        """
        Cria comissão para o referrer do user pago.
        - Se user não tem referrer → no-op.
        - Se valor inválido/zero → no-op.
        - Idempotente: se já existe commission para esse cakto_transaction_id, retorna None.
        """
        if not referred_user.referrer_user_id:
            return None

        try:
            base = Decimal(str(amount)) if amount is not None else Decimal(0)
        except Exception:
            logger.warning("create_commission: valor inválido (%s) — ignorando", amount)
            return None

        if base <= 0:
            return None

        if cakto_transaction_id and self.repo.exists_for_transaction(cakto_transaction_id):
            logger.info(
                "create_commission: comissão já existe para tx=%s — idempotente",
                cakto_transaction_id,
            )
            return None

        commission = Commission(
            referrer_user_id=referred_user.referrer_user_id,
            referred_user_id=referred_user.id,
            subscription_id=subscription.id if subscription else None,
            cakto_transaction_id=cakto_transaction_id,
            base_amount=base,
            rate=DEFAULT_COMMISSION_RATE,
            amount=(base * DEFAULT_COMMISSION_RATE).quantize(Decimal("0.01")),
            status="pending",
        )
        created = self.repo.create(commission)
        logger.info(
            "Comissão criada: id=%s referrer=%s referred=%s amount=%s tx=%s",
            created.id,
            commission.referrer_user_id,
            commission.referred_user_id,
            commission.amount,
            cakto_transaction_id,
        )
        return created
