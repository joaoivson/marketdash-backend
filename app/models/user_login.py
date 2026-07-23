from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.sql import func

from app.db.base import Base


class UserLogin(Base):
    __tablename__ = "user_logins"

    id = Column(BigInteger, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    logged_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    ip = Column(Text, nullable=True)
    user_agent = Column(Text, nullable=True)
