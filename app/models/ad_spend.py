from sqlalchemy import Column, Integer, String, Float, Date, ForeignKey
from sqlalchemy.orm import relationship
from app.db.base import Base

class AdSpend(Base):
    __tablename__ = "ad_spends"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    sub_id = Column(String, nullable=True, index=True)  # ex: dispenser01
    amount = Column(Float, nullable=False)

    # Relationships
    user = relationship("User", back_populates="ad_spends")
