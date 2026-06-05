from sqlalchemy import (
    Column,
    Integer,
    String,
    Float,
    Date,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class Campaign(Base):
    """Campanha de anúncio sincronizada da Facebook Marketing API.

    O gasto/cliques/orçamento vêm do Facebook; comissão/pedidos vêm de
    DatasetRow (Shopee) via o vínculo manual `sub_id` → DatasetRow.sub_id1.
    """

    __tablename__ = "campaigns"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # Identificadores do Facebook
    fb_campaign_id = Column(String(64), nullable=False, index=True)
    ad_account_id = Column(String(64), nullable=True)

    name = Column(String(512), nullable=False)
    objective = Column(String(128), nullable=True)

    # Status configurado pelo usuário (ACTIVE / PAUSED) e status efetivo do FB
    # (ACTIVE, PAUSED, CAMPAIGN_PAUSED, ARCHIVED, WITH_ISSUES, ...).
    status = Column(String(32), nullable=True)
    effective_status = Column(String(64), nullable=True)

    # Orçamento em BRL (convertido dos "centavos" da API do Facebook).
    daily_budget = Column(Float, nullable=True)
    lifetime_budget = Column(Float, nullable=True)

    # Vínculo manual com o Sub ID da Shopee (DatasetRow.sub_id1). NULL = "não vinculada".
    sub_id = Column(String(255), nullable=True, index=True)

    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="campaigns")
    daily_insights = relationship(
        "CampaignDailyInsight",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("user_id", "fb_campaign_id", name="uq_campaign_user_fb"),
        Index("idx_campaign_user_subid", "user_id", "sub_id"),
    )


class CampaignDailyInsight(Base):
    """Métricas diárias de uma campanha (Facebook Insights, level=campaign)."""

    __tablename__ = "campaign_daily_insights"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False, index=True)
    fb_campaign_id = Column(String(64), nullable=True)

    date = Column(Date, nullable=False, index=True)

    spend = Column(Float, nullable=False, default=0.0)
    clicks = Column(Integer, nullable=False, default=0)
    impressions = Column(Integer, nullable=False, default=0)
    cpc = Column(Float, nullable=True)
    ctr = Column(Float, nullable=True)
    reach = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationship
    campaign = relationship("Campaign", back_populates="daily_insights")

    __table_args__ = (
        UniqueConstraint("campaign_id", "date", name="uq_insight_campaign_date"),
        Index("idx_insight_user_date", "user_id", "date"),
    )
