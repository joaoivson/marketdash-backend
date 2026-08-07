"""Opt-in do resumo diário no WhatsApp e o log de envios."""
from sqlalchemy import (
    BigInteger, Column, Date, DateTime, ForeignKey, Integer, Text,
)
from sqlalchemy.sql import func

from app.db.base import Base

STATUS_PENDENTE = "pendente"
STATUS_CONFIRMADO = "confirmado"
STATUS_DESLIGADO = "desligado"

TIPO_RESUMO = "resumo_diario"
TIPO_CONFIRMACAO = "confirmacao"

ENVIO_OK = "enviado"
ENVIO_FALHOU = "falhou"

# Quem desligou: a própria afiliada no app, ela respondendo SAIR, ou nós
# (número que só devolve erro não continua no lote).
ORIGEM_APP = "app"
ORIGEM_WHATSAPP = "whatsapp"
ORIGEM_FALHA = "falha"


class WhatsappOptin(Base):
    __tablename__ = "whatsapp_optins"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, unique=True, index=True)
    numero = Column(Text, nullable=False, index=True)
    status = Column(Text, nullable=False, default=STATUS_PENDENTE, index=True)
    codigo = Column(Text, nullable=True)
    codigo_expira_em = Column(DateTime(timezone=True), nullable=True)
    tentativas = Column(Integer, nullable=False, default=0)
    confirmado_em = Column(DateTime(timezone=True), nullable=True)
    desligado_em = Column(DateTime(timezone=True), nullable=True)
    desligado_por = Column(Text, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WhatsappEnvio(Base):
    __tablename__ = "whatsapp_envios"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    tipo = Column(Text, nullable=False)
    referencia = Column(Date, nullable=True)
    status = Column(Text, nullable=False)
    erro = Column(Text, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
