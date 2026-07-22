"""Model de mapeamento Kiwify product_id → plano/período."""

from sqlalchemy import Column, Integer, String, Text, DateTime
from sqlalchemy.sql import func
from app.db.base import Base


class KiwifyPlanProduct(Base):
    __tablename__ = "kiwify_plan_products"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(String(128), unique=True, nullable=False, index=True)
    plano = Column(String(32), nullable=False)
    periodo = Column(String(32), nullable=False)
    checkout_url = Column(Text, nullable=True)
    label = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
