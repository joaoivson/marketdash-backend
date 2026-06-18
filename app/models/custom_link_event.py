from sqlalchemy import Column, Integer, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.db.base import Base


class CustomLinkEvent(Base):
    """1 evento por clique contabilizado (pós-dedup/bot), com timestamp.

    Log paralelo ao click_count: este último segue sendo o total verdadeiro;
    aqui guardamos o instante de cada clique para alimentar a série do insight.
    FORWARD-ONLY — só existem eventos a partir da migration 032.
    """

    __tablename__ = "custom_link_events"

    id = Column(Integer, primary_key=True, index=True)
    custom_link_id = Column(
        Integer,
        ForeignKey("custom_links.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_custom_link_events_link_created", "custom_link_id", "created_at"),
    )
