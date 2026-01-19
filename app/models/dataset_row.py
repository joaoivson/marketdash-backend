from sqlalchemy import Column, Integer, String, Numeric, Date, Time, ForeignKey, Index, JSON
from sqlalchemy.orm import relationship
from app.db.base import Base


class DatasetRow(Base):
    __tablename__ = "dataset_rows"

    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    
    # Campos temporais
    date = Column(Date, nullable=False, index=True)
    transaction_date = Column(Date, nullable=True, index=True)  # Alias para date, mantém compatibilidade
    time = Column(Time, nullable=True, index=True)  # Horário separado
    
    # Dimensões
    product = Column(String, nullable=False, index=True)
    product_name = Column(String, nullable=True, index=True)  # Alias para product
    platform = Column(String, nullable=True, index=True)
    status = Column(String, nullable=True, index=True)  # Status do pedido
    category = Column(String, nullable=True, index=True)  # Categoria Global L1
    sub_id1 = Column(String, nullable=True, index=True)
    mes_ano = Column(String, nullable=True, index=True)  # formato YYYY-MM
    raw_data = Column(JSON, nullable=True)  # dados completos da linha
    
    # Métricas financeiras (campos originais para compatibilidade)
    revenue = Column(Numeric(12, 2), nullable=True)
    cost = Column(Numeric(12, 2), nullable=True)
    commission = Column(Numeric(12, 2), nullable=True)
    profit = Column(Numeric(12, 2), nullable=True)
    
    # Métricas analíticas (campos do Power BI)
    gross_value = Column(Numeric(12, 2), nullable=True, index=True)  # Total de vendas
    commission_value = Column(Numeric(12, 2), nullable=True, index=True)  # Valor da comissão
    net_value = Column(Numeric(12, 2), nullable=True, index=True)  # Valor líquido
    quantity = Column(Integer, nullable=True, default=1)  # Quantidade de vendas

    # Relationships
    dataset = relationship("Dataset", back_populates="rows")
    user = relationship("User", back_populates="dataset_rows")

    # Composite indexes for analytics queries (otimização para BI)
    __table_args__ = (
        Index('idx_user_date', 'user_id', 'date'),
        Index('idx_user_product', 'user_id', 'product'),
        Index('idx_user_date_product', 'user_id', 'date', 'product'),
        Index('idx_user_platform', 'user_id', 'platform'),
        Index('idx_user_transaction_date', 'user_id', 'transaction_date'),
        Index('idx_user_product_platform', 'user_id', 'product', 'platform'),
        Index('idx_date_platform', 'date', 'platform'),
        Index('idx_user_sub_id_date', 'user_id', 'sub_id1', 'date'),
    )

