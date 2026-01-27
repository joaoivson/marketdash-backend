from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    plan = Column(String, default="free")  # free, marketdash
    is_active = Column(Boolean, default=False)
    last_validation_at = Column(DateTime(timezone=True), nullable=True)
    cakto_customer_id = Column(String(255), nullable=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    cakto_transaction_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", backref="subscription")

