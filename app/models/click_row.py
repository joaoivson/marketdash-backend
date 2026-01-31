from sqlalchemy import Column, Integer, String, Date, Time, ForeignKey, Index, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class ClickRow(Base):
    __tablename__ = "click_rows"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Campos fundamentais
    date = Column(Date, nullable=False, index=True)
    channel = Column(String, nullable=False, index=True)
    sub_id = Column(String, nullable=True, index=True)
    clicks = Column(Integer, nullable=False, default=0)
    
    row_hash = Column(String(32), nullable=True, unique=True, index=True)

    # Relationships
    dataset = relationship("Dataset", backref="click_rows")
    user = relationship("User", back_populates="click_rows")

    # Composite indexes for performance
    __table_args__ = (
        Index('idx_click_user_report', 'user_id', 'date', 'channel'),
    )
