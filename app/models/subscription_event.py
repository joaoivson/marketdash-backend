"""Evento append-only do webhook Kiwify (histórico para MRR/churn/faturamento)."""
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.base import Base


class SubscriptionEvent(Base):
    __tablename__ = "subscription_events"

    id = Column(BigInteger, primary_key=True, index=True)
    event_type = Column(Text, nullable=False)
    order_id = Column(Text, nullable=True)
    order_ref = Column(Text, nullable=True)
    order_status = Column(Text, nullable=True)
    subscription_id = Column(Text, nullable=True)
    customer_email = Column(Text, nullable=True)
    customer_name = Column(Text, nullable=True)
    customer_cpf = Column(Text, nullable=True)
    customer_phone = Column(Text, nullable=True)
    plan_id = Column(Text, nullable=True)
    plan_name = Column(Text, nullable=True)
    plan_frequency = Column(Text, nullable=True)
    amount_gross_cents = Column(Integer, nullable=True)
    fee_cents = Column(Integer, nullable=True)
    amount_net_cents = Column(Integer, nullable=True)
    payment_method = Column(Text, nullable=True)
    subscription_status = Column(Text, nullable=True)
    has_access = Column(Boolean, nullable=True)
    access_until = Column(DateTime(timezone=True), nullable=True)
    next_payment = Column(DateTime(timezone=True), nullable=True)
    subscription_start = Column(DateTime(timezone=True), nullable=True)
    approved_date = Column(DateTime(timezone=True), nullable=True)
    refunded_at = Column(DateTime(timezone=True), nullable=True)
    funds_status = Column(Text, nullable=True)
    deposit_date = Column(DateTime(timezone=True), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    is_plan_change = Column(Boolean, nullable=False, default=False)
    raw_payload = Column(JSONB, nullable=False, server_default="{}")
    received_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    dedupe_key = Column(String, nullable=False, unique=True)
