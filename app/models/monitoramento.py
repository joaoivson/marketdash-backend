"""Monitoramento de grupos e capturas — F8 (espelho da 066).

**Nenhuma coluna guarda quem escreveu.** Não há JID, telefone nem hash de
autor: só o texto e o hash do próprio texto, usado para deduplicar repost.
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey, Index, Integer, String,
    Text, UniqueConstraint, text,
)
from sqlalchemy.sql import func

from app.db.base import Base
from app.models.tipos import JSON_PORTATIL

CAPTURA_CAPTURADA = "capturada"
# Estado intermediário do claim: quem consegue mover `capturada` → `replicando`
# é o único que replica. Sem ele, dois workers leem "capturada" ao mesmo tempo
# e a mesma oferta sai duas vezes para os grupos dela.
CAPTURA_REPLICANDO = "replicando"
CAPTURA_REPLICADA = "replicada"
CAPTURA_IGNORADA = "ignorada"
CAPTURA_ERRO = "erro"


class Monitoramento(Base):
    __tablename__ = "monitoramentos"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False)
    nome = Column(String(120), nullable=False)
    grupo_origem_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                             nullable=False)
    instancia_id = Column(Integer, ForeignKey("whatsapp_instancias.id", ondelete="SET NULL"),
                          nullable=True)
    destino_campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="SET NULL"),
                                 nullable=True)
    destino_grupo_ids = Column(JSON_PORTATIL, nullable=True)
    ativo = Column(Boolean, nullable=False, default=False, server_default="false")
    converter_links = Column(Boolean, nullable=False, default=True,
                             server_default="true")
    somente_com_link = Column(Boolean, nullable=False, default=True,
                              server_default="true")
    palavras_chave = Column(JSON_PORTATIL, nullable=True)
    # Padrão FALSE: replicar sem revisão manda para os grupos dela um texto que
    # outra pessoa escreveu e ninguém leu.
    replicar_automaticamente = Column(Boolean, nullable=False, default=False,
                                      server_default="false")
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False,
                           server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # MESMOS nomes da 066: nomes diferentes fazem o create_all criar um
        # índice idêntico ao lado do da migration, e o parcial do caminho quente
        # do webhook (`WHERE ativo`) simplesmente não existe.
        Index("ix_monitoramentos_origem_ativo", "grupo_origem_id",
              postgresql_where=text("ativo")),
        Index("ix_monitoramentos_user", "user_id"),
    )


class MonitoramentoCaptura(Base):
    __tablename__ = "monitoramento_capturas"
    __table_args__ = (
        UniqueConstraint("monitoramento_id", "mensagem_hash",
                         name="uq_captura_por_monitoramento"),
        Index("ix_capturas_monitoramento_criado", "monitoramento_id",
              text("criado_em DESC")),
    )

    id = Column(BigInteger, primary_key=True)
    monitoramento_id = Column(Integer, ForeignKey("monitoramentos.id", ondelete="CASCADE"),
                              nullable=False)
    mensagem_hash = Column(String(64), nullable=False)
    texto_original = Column(Text, nullable=True)
    texto_final = Column(Text, nullable=True)
    link_original = Column(Text, nullable=True)
    link_convertido = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=CAPTURA_CAPTURADA,
                    server_default=CAPTURA_CAPTURADA)
    motivo = Column(String(200), nullable=True)
    roteiro_id = Column(Integer, ForeignKey("roteiros.id", ondelete="SET NULL"),
                        nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    replicado_em = Column(DateTime(timezone=True), nullable=True)
