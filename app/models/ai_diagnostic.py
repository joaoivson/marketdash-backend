"""Sessão de diagnóstico e as mensagens do chat dela."""
from sqlalchemy import (
    BigInteger, Column, Date, DateTime, ForeignKey, Integer, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.base import Base

STATUS_GERANDO = "gerando"
STATUS_PRONTO = "pronto"
STATUS_ERRO = "erro"

PAPEL_USUARIA = "user"
PAPEL_IA = "assistant"


class AiDiagnostic(Base):
    __tablename__ = "ai_diagnostics"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    periodo_inicio = Column(Date, nullable=False)
    periodo_fim = Column(Date, nullable=False)
    # snapshot: os números CONGELADOS. O chat lê daqui, nunca de dados frescos —
    # é o que garante que a conversa nunca contradiga o PDF.
    snapshot = Column(JSONB, nullable=False, server_default="{}")
    relatorio = Column(JSONB, nullable=True)
    status = Column(Text, nullable=False, default=STATUS_GERANDO)
    erro_mensagem = Column(Text, nullable=True)
    modelo = Column(Text, nullable=True)
    tokens_entrada = Column(Integer, nullable=True)
    tokens_saida = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    concluido_em = Column(DateTime(timezone=True), nullable=True)


class AiDiagnosticMessage(Base):
    __tablename__ = "ai_diagnostic_messages"

    id = Column(BigInteger, primary_key=True, index=True)
    diagnostic_id = Column(
        BigInteger, ForeignKey("ai_diagnostics.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    papel = Column(Text, nullable=False)
    conteudo = Column(Text, nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
