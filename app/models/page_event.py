from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Index
from sqlalchemy.sql import func
from app.db.base import Base


class PageEvent(Base):
    __tablename__ = "page_events"

    id = Column(Integer, primary_key=True, index=True)
    site_id = Column(Integer, ForeignKey("capture_sites.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String, nullable=False)  # page_view, click_group
    utm_source = Column(String, nullable=True)
    utm_medium = Column(String, nullable=True)
    utm_campaign = Column(String, nullable=True)
    utm_adset = Column(String, nullable=True)
    utm_ad = Column(String, nullable=True)
    referrer = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    ip_address = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_page_events_site_created", "site_id", "created_at"),
    )
