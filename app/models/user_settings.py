from app.models.tipos import JSON_PORTATIL
from sqlalchemy import Column, Integer, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.db.base import Base


class UserSettings(Base):
    __tablename__ = "user_settings"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    ad_tax_rate = Column(Float, nullable=False, default=0.0)
    commission_tax_rate = Column(Float, nullable=False, default=0.0)
    # Janela de envio do módulo de grupos (F3) — shape validado por Pydantic
    # em janela_envio_service; NULL = padrão 08:00-22:00 todos os dias.
    whatsapp_envio_config = Column(JSON_PORTATIL, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user = relationship("User", back_populates="settings")
