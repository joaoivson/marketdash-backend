"""Campanhas de grupos de WhatsApp — Módulo de Grupos F2 (espelho da 059).

"Campanha" aqui é o conjunto de grupos com link de entrada, roteiros e
métricas (spec §2) — NÃO confundir com app/models/campaign.py, que é a
campanha de TRÁFEGO PAGO do Meta (a tela que virou "Anúncios").
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
)
from sqlalchemy.sql import func

from app.db.base import Base

CAMPANHA_ATIVA = "ativa"
CAMPANHA_PAUSADA = "pausada"
CAMPANHA_ARQUIVADA = "arquivada"

ESTRATEGIA_SEQUENCIAL = "sequencial"
ESTRATEGIA_ALEATORIA = "aleatoria"

MODO_IMAGEM_LINK = "link_preview"
MODO_IMAGEM_NORMAL = "imagem_normal"


class Campanha(Base):
    __tablename__ = "campanhas"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    nome = Column(String(120), nullable=False)
    descricao = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default=CAMPANHA_ATIVA)
    estrategia_entrada = Column(String(16), nullable=False, default=ESTRATEGIA_SEQUENCIAL)
    abertura_automatica = Column(Boolean, nullable=False, default=True)
    reabertura_automatica = Column(Boolean, nullable=False, default=True)
    prefixo = Column(Text, nullable=True)
    sufixo = Column(Text, nullable=True)
    modo_imagem = Column(String(16), nullable=False, default=MODO_IMAGEM_LINK)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CampanhaGrupo(Base):
    __tablename__ = "campanha_grupos"

    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         primary_key=True)
    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      primary_key=True)
    posicao = Column(Integer, nullable=False, default=0)
    aberto = Column(Boolean, nullable=False, default=True)
    adicionado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
