from sqlalchemy import Column, Integer, String, Date, Time, ForeignKey, Index, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class ClickRow(Base):
    __tablename__ = "click_rows"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Campos temporais
    date = Column(Date, nullable=False, index=True)
    time = Column(Time, nullable=True, index=True)
    
    # Dimensões
    channel = Column(String, nullable=False, index=True)  # Canal (Instagram, Facebook, etc)
    sub_id = Column(String, nullable=True, index=True)
    
    # Métricas
    clicks = Column(Integer, nullable=False, default=0)
    
    row_hash = Column(String(32), nullable=True, index=True)  # Hash MD5 para deduplicação
    raw_data = Column(JSON, nullable=True)  # dados completos da linha

    # Relationships
    dataset = relationship("Dataset", backref="click_rows")
    user = relationship("User", back_populates="click_rows")

    # Composite indexes for analytics queries
    __table_args__ = (
        Index('idx_click_user_date', 'user_id', 'date'),
        Index('idx_click_user_channel', 'user_id', 'channel'),
        Index('idx_click_user_sub_id', 'user_id', 'sub_id'),
        Index('idx_click_date_channel', 'date', 'channel'),
    )
