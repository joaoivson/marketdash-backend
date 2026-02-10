from sqlalchemy import Column, Integer, String, Numeric, Date, Time, ForeignKey, Index
from sqlalchemy.orm import relationship
from app.db.base import Base


class DatasetRow(Base):
    __tablename__ = "dataset_rows_v2"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Dimensões (Campos de agrupamento) — data e hora separadas quando CSV traz datetime
    date = Column(Date, nullable=False, index=True)
    time = Column(Time, nullable=True)
    platform = Column(String, nullable=True, index=True)
    category = Column(String, nullable=True, index=True)
    product = Column(String, nullable=False, index=True)
    status = Column(String, nullable=True, index=True)
    sub_id1 = Column(String, nullable=True, index=True)
    order_id = Column(String, nullable=True, index=True)
    product_id = Column(String, nullable=True, index=True)
    
    # Métricas (Somadas no agrupamento)
    revenue = Column(Numeric(12, 2), nullable=True, default=0)
    commission = Column(Numeric(12, 2), nullable=True, default=0)
    cost = Column(Numeric(12, 2), nullable=True, default=0) # Custo de anúncios aplicado
    profit = Column(Numeric(12, 2), nullable=True, default=0)
    quantity = Column(Integer, nullable=True, default=1)
    
    # Identificador único para deduplicação (Data + Plataforma + Categoria + Produto + Status + SubID)
    row_hash = Column(String(32), nullable=True, unique=True, index=True)

    # Relationships
    dataset = relationship("Dataset", back_populates="rows")
    user = relationship("User", back_populates="dataset_rows")

    # Composite indexes for analytics queries (otimização para BI)
    __table_args__ = (
        Index('idx_dataset_rows_user_report', 'user_id', 'date', 'platform', 'product'),
        Index('idx_dataset_rows_user_category', 'user_id', 'category'),
        Index('idx_dataset_rows_user_sub_id', 'user_id', 'sub_id1'),
    )
