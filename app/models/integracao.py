"""Integrações de marketplace por usuária — F5 (espelho da 062).

Substitui `shopee_integrations` (uma conta, um marketplace) por N contas de N
marketplaces. Sem "principal": a integração certa é resolvida pelo marketplace
detectado na URL do produto.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.base import Base

PROVEDOR_SHOPEE = "shopee"
# Só entram aqui provedores com API de afiliado aberta e assinada. Mercado
# Livre e SHEIN NÃO têm (o Orbit converte por extensão de Chrome) — prometer
# marketplace sem API vira dívida de suporte.
PROVEDORES = (PROVEDOR_SHOPEE,)


class Integracao(Base):
    __tablename__ = "integracoes"
    __table_args__ = (
        UniqueConstraint("user_id", "provedor", "label", name="uq_integracoes"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    provedor = Column(String(24), nullable=False)
    label = Column(String(64), nullable=False, default="principal")
    # JSON cifrado (Fernet). Nunca consultado por dentro — só decifrado inteiro.
    credenciais = Column(Text, nullable=False)
    ativa = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
