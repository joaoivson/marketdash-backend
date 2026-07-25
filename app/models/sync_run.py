from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.base import Base


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id = Column(BigInteger, primary_key=True)
    source = Column(Text, nullable=False)
    trigger = Column(Text, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    days_back = Column(Integer, nullable=True)
    empty_attempt = Column(Integer, nullable=False, default=0)
    status = Column(Text, nullable=False, default="running")
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    records_fetched = Column(Integer, nullable=True)
    records_upserted = Column(Integer, nullable=True)
    is_suspected_partial = Column(Boolean, nullable=False, default=False)
    error_message = Column(Text, nullable=True)
    details = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
