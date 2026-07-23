from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, Integer, Text
from sqlalchemy.sql import func

from app.db.base import Base


class Expense(Base):
    __tablename__ = "expenses"

    id = Column(BigInteger, primary_key=True)
    date = Column(Date, nullable=False, index=True)
    category = Column(Text, nullable=False)
    supplier = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    amount_cents = Column(Integer, nullable=False)
    recurring = Column(Boolean, nullable=False, default=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=True, onupdate=func.now())
