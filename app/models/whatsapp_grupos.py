"""Números conectados (sessões WAHA) e grupos da afiliada — Módulo de Grupos F1.

Espelho da migration 058. As tabelas do resumo diário (whatsapp_optins/envios)
vivem em app/models/whatsapp.py e não mudam.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.base import Base

INSTANCIA_CRIADA = "criada"
INSTANCIA_CONECTADA = "conectada"
INSTANCIA_DESCONECTADA = "desconectada"
INSTANCIA_REMOVIDA = "removida"


class WhatsappInstancia(Base):
    __tablename__ = "whatsapp_instancias"

    id = Column(Integer, primary_key=True, index=True)
    # SEM unique: multi-número desde o v1 (spec §4.2).
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    nome_exibicao = Column(String(120), nullable=True)
    # Chave de roteamento do webhook: mkd{ref4}u{user_id}x{hex4}.
    nome_instancia = Column(String(64), nullable=False, unique=True)
    numero = Column(String(32), nullable=True)
    status = Column(String(16), nullable=False, default=INSTANCIA_CRIADA)
    teto_diario = Column(Integer, nullable=True)   # NULL = default do sistema
    falhas_seguidas = Column(Integer, nullable=False, default=0)
    ultima_conexao_em = Column(DateTime(timezone=True), nullable=True)
    # --- proxy por sessão (migration 068). STICKY: o chip fica com um IP fixo
    # enquanto estiver saudável — trocar de IP é o que denuncia automação.
    # `proxy_trocas` existe para que a troca seja um evento medido, não rotina.
    proxy_id = Column(Integer, ForeignKey("whatsapp_proxies.id", ondelete="SET NULL"),
                      nullable=True, index=True)
    proxy_fixado_em = Column(DateTime(timezone=True), nullable=True)
    proxy_trocas = Column(Integer, nullable=False, default=0)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WhatsappGrupo(Base):
    __tablename__ = "whatsapp_grupos"
    # Espelha a constraint da migration 058: se o create_all chegar antes dela
    # (a corrida que o protocolo tenta impedir), a tabela ainda nasce com o
    # guard que impede grupo duplicado com dois sub_ids.
    __table_args__ = (UniqueConstraint("user_id", "jid", name="uq_whatsapp_grupos_user_jid"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    jid = Column(String(64), nullable=False)
    nome = Column(String(255), nullable=True)
    foto_url = Column(Text, nullable=True)
    # AGREGADO (contagem) — a lista de membros nunca toca o banco (LGPD).
    participantes = Column(Integer, nullable=False, default=0)
    capacidade = Column(Integer, nullable=False, default=1024)
    sou_admin = Column(Boolean, nullable=False, default=False)
    permite_envio = Column(Boolean, nullable=False, default=False)
    link_convite = Column(Text, nullable=True)
    categoria = Column(String(64), nullable=True)
    # False quando some do sync — NUNCA deletar (atribuição histórica).
    ativo = Column(Boolean, nullable=False, default=True)
    sub_id = Column(String(24), nullable=True, unique=True)
    custom_link_id = Column(Integer, ForeignKey("custom_links.id", ondelete="SET NULL"),
                            nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WhatsappGrupoInstancia(Base):
    __tablename__ = "whatsapp_grupo_instancias"

    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      primary_key=True)
    instancia_id = Column(Integer, ForeignKey("whatsapp_instancias.id", ondelete="CASCADE"),
                          primary_key=True)
    sou_admin = Column(Boolean, nullable=False, default=False)
