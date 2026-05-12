from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.commission import Commission
from app.models.user import User


class CommissionRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, commission: Commission) -> Commission:
        self.db.add(commission)
        self.db.flush()
        return commission

    def exists_for_transaction(self, cakto_transaction_id: str) -> bool:
        if not cakto_transaction_id:
            return False
        return (
            self.db.query(Commission.id)
            .filter(Commission.cakto_transaction_id == cakto_transaction_id)
            .first()
            is not None
        )

    def get_by_referrer(self, referrer_user_id: int) -> List[Commission]:
        return (
            self.db.query(Commission)
            .filter(Commission.referrer_user_id == referrer_user_id)
            .order_by(Commission.created_at.desc())
            .all()
        )

    def get_by_ids(self, ids: List[int]) -> List[Commission]:
        if not ids:
            return []
        return self.db.query(Commission).filter(Commission.id.in_(ids)).all()

    def sum_by_status(self, referrer_user_id: int, status: str) -> Decimal:
        total = (
            self.db.query(func.coalesce(func.sum(Commission.amount), 0))
            .filter(
                Commission.referrer_user_id == referrer_user_id,
                Commission.status == status,
            )
            .scalar()
        )
        return Decimal(total or 0)

    def count_distinct_referred(self, referrer_user_id: int) -> int:
        return (
            self.db.query(func.count(func.distinct(Commission.referred_user_id)))
            .filter(Commission.referrer_user_id == referrer_user_id)
            .scalar()
            or 0
        )

    def pending_aggregated(self) -> List[dict]:
        """Lista afiliados com saldo > 0 (somente comissões pending), com PIX e contagem."""
        rows = (
            self.db.query(
                User.id.label("referrer_user_id"),
                User.name,
                User.email,
                User.pix_key,
                func.coalesce(func.sum(Commission.amount), 0).label("total_pending"),
                func.count(Commission.id).label("commissions_count"),
                func.array_agg(Commission.id).label("commission_ids"),
            )
            .join(Commission, Commission.referrer_user_id == User.id)
            .filter(Commission.status == "pending")
            .group_by(User.id, User.name, User.email, User.pix_key)
            .order_by(func.sum(Commission.amount).desc())
            .all()
        )
        return [
            {
                "referrer_user_id": r.referrer_user_id,
                "name": r.name,
                "email": r.email,
                "pix_key": r.pix_key,
                "total_pending": Decimal(r.total_pending or 0),
                "commissions_count": int(r.commissions_count or 0),
                "commission_ids": list(r.commission_ids or []),
            }
            for r in rows
        ]

    def mark_paid_bulk(self, ids: List[int], payment_reference: str) -> tuple[int, Decimal]:
        if not ids:
            return 0, Decimal(0)
        now = datetime.now(timezone.utc)
        commissions = (
            self.db.query(Commission)
            .filter(Commission.id.in_(ids), Commission.status == "pending")
            .all()
        )
        total = Decimal(0)
        for c in commissions:
            c.status = "paid"
            c.paid_at = now
            c.payment_reference = payment_reference
            total += Decimal(c.amount)
        self.db.flush()
        return len(commissions), total
