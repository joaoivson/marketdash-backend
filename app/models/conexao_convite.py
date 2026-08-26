"""Convite de conexão externa — item 18 da spec (espelho da 067).

A afiliada gera um link temporário para OUTRA pessoa escanear o QR (uma
assistente, o dono do número) sem dar acesso à conta dela no MarketDash.

Só o hash do token é guardado: o segredo vive no link que ela enviou, não aqui.
Quem lê o banco não consegue abrir a tela de pareamento.
"""
from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.sql import func

from app.db.base import Base


class ConexaoConvite(Base):
    __tablename__ = "conexao_convites"
    __table_args__ = (
        Index("ix_conexao_convites_instancia", "instancia_id"),
    )

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    instancia_id = Column(Integer, ForeignKey("whatsapp_instancias.id",
                                              ondelete="CASCADE"), nullable=False)
    token_hash = Column(String(64), nullable=False, unique=True)
    expira_em = Column(DateTime(timezone=True), nullable=False)
    # Marcado quando a sessão conecta: o link morre na hora, não no fim do
    # prazo. Link de pareamento válido depois de pareado é um convite para
    # outra pessoa conectar OUTRO número no lugar.
    usado_em = Column(DateTime(timezone=True), nullable=True)
    revogado_em = Column(DateTime(timezone=True), nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False,
                       server_default=func.now())
