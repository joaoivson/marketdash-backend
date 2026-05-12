from sqlalchemy import Column, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base


class Commission(Base):
    __tablename__ = "commissions"

    id = Column(Integer, primary_key=True, index=True)
    referrer_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    referred_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subscription_id = Column(Integer, ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True)

    # Idempotência: evita criar duas comissões para o mesmo evento Cakto.
    cakto_transaction_id = Column(String(255), nullable=True, unique=True)

    amount = Column(Numeric(10, 2), nullable=False)
    base_amount = Column(Numeric(10, 2), nullable=False)
    rate = Column(Numeric(5, 4), nullable=False, default=0.40)
    status = Column(String(32), nullable=False, default="pending")  # pending | paid | cancelled
    paid_at = Column(DateTime(timezone=True), nullable=True)
    payment_reference = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    referrer = relationship("User", foreign_keys=[referrer_user_id])
    referred = relationship("User", foreign_keys=[referred_user_id])
