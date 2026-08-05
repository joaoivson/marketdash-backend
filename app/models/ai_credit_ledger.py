"""
Extrato de créditos de IA.

Saldo é DERIVADO da soma do mês corrente, não guardado num contador: contador
diverge silenciosamente, extrato permite auditar por que uma aluna zerou.
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.sql import func

from app.db.base import Base

TIPO_GERACAO = "geracao"
TIPO_CHAT = "chat"


class AiCreditLedger(Base):
    __tablename__ = "ai_credit_ledger"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    diagnostic_id = Column(
        BigInteger, ForeignKey("ai_diagnostics.id", ondelete="SET NULL"), nullable=True,
    )
    tipo = Column(Text, nullable=False)
    creditos = Column(Integer, nullable=False)
    saldo_apos = Column(Integer, nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
