"""Vínculo N:1 entre campanhas de anúncio do Meta e campanha de grupos — F7."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.sql import func

from app.db.base import Base


class CampanhaAnuncio(Base):
    __tablename__ = "campanha_anuncios"

    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         primary_key=True)
    # unique=True carrega a regra de negócio: uma campanha do Meta pertence a
    # UMA campanha de grupos. Sem isso o mesmo gasto é atribuído duas vezes.
    campaign_id = Column(Integer, ForeignKey("campaigns.id", ondelete="CASCADE"),
                         primary_key=True, index=True, unique=True)
    vinculado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
