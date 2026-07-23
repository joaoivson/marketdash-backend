from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.sql import func

from app.db.base import Base


class PageView(Base):
    __tablename__ = "page_views"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    path = Column(Text, nullable=False)
    viewed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
