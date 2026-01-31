from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.db.base import Base

class AdSpend(Base):
    __tablename__ = "ad_spends"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    sub_id = Column(String, nullable=True, index=True)  # ex: dispenser01
    amount = Column(Float, nullable=False)
    clicks = Column(Integer, nullable=True, default=0)

    # Relationships
    user = relationship("User", back_populates="ad_spends")

    __table_args__ = (
        Index("idx_ad_spend_user_date", "user_id", "date"),
        Index("idx_ad_spend_user_sub_date", "user_id", "sub_id", "date"),
        Index("idx_ad_spend_user_date_id", "user_id", "date", "id"),
    )
