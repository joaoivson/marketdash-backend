"""Link de entrada, eventos de clique/entrada e snapshots — F6 (espelho da 063)."""
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Integer,
    String, Text,
)
from app.models.tipos import JSON_PORTATIL
from sqlalchemy.sql import func

from app.db.base import Base

EVENTO_ENTRADA = "entrada"
EVENTO_SAIDA = "saida"

ORIGEM_LINK = "link"
ORIGEM_ORGANICA = "organica"
ORIGEM_DESCONHECIDA = "desconhecida"


class CampanhaLink(Base):
    __tablename__ = "campanha_links"

    id = Column(Integer, primary_key=True, index=True)
    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    slug = Column(String(64), nullable=False, unique=True)
    titulo_previa = Column(String(160), nullable=True)
    descricao_previa = Column(String(300), nullable=True)
    banner_previa_url = Column(Text, nullable=True)
    pixel_facebook_id = Column(String(32), nullable=True)
    pixel_eventos = Column(JSON_PORTATIL, nullable=False,
                           default=lambda: {"pageview": True, "lead": True})
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CampanhaLinkEvento(Base):
    __tablename__ = "campanha_link_eventos"

    id = Column(BigInteger, primary_key=True, index=True)
    link_id = Column(Integer, ForeignKey("campanha_links.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="SET NULL"),
                      nullable=True, index=True)
    ip_hash = Column(String(64), nullable=True)
    user_agent = Column(Text, nullable=True)
    referer = Column(Text, nullable=True)
    is_teste = Column(Boolean, nullable=False, default=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GrupoEvento(Base):
    __tablename__ = "grupo_eventos"

    id = Column(BigInteger, primary_key=True, index=True)
    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    tipo = Column(String(8), nullable=False)
    origem = Column(String(16), nullable=False, default=ORIGEM_DESCONHECIDA)
    link_evento_id = Column(BigInteger,
                            ForeignKey("campanha_link_eventos.id", ondelete="SET NULL"),
                            nullable=True)
    # sha256(jid + WHATSAPP_HASH_SALT). O número de quem entra NUNCA persiste.
    identificador_hash = Column(String(64), nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GrupoSnapshot(Base):
    __tablename__ = "grupo_snapshots"

    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      primary_key=True)
    data = Column(Date, primary_key=True)
    participantes = Column(Integer, nullable=False, default=0)
    admins = Column(Integer, nullable=False, default=0)
