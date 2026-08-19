"""Modelos da automação de Instagram (comentário → direct).

Conexão via **Business Login for Instagram** (host `graph.instagram.com`), com
credenciais próprias dentro do mesmo app da Meta. Independente da conexão de
anúncios: dois tokens, dois ciclos de renovação, dois hosts.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.base import Base

# JSONB no Postgres (indexável, é o que a migration cria) e JSON genérico em
# qualquer outro dialeto — os testes unitários rodam em SQLite, que não conhece
# JSONB e quebraria no create_all.
JsonCol = JSON().with_variant(JSONB, "postgresql")

# Status possíveis, espelhando as migrations.
CONEXAO_ATIVA = "ativo"
CONEXAO_EXPIRADA = "expirado"
CONEXAO_REVOGADA = "revogado"

AUTOMACAO_ATIVA = "ativa"
AUTOMACAO_PAUSADA = "pausada"
AUTOMACAO_RASCUNHO = "rascunho"

ESCOPO_POST_ESPECIFICO = "post_especifico"
ESCOPO_QUALQUER = "qualquer"
ESCOPO_PROXIMO = "proximo"

TRIGGER_PALAVRAS = "palavras"
TRIGGER_QUALQUER = "qualquer"

# dm_status
DM_ENVIADO = "enviado"
DM_FALHOU = "falhou"
DM_EXPIRADO = "expirado"
DM_DUPLICADO = "duplicado"
DM_IGNORADO = "ignorado"
DM_SEM_MATCH = "sem_match"
# Estado TRANSITÓRIO de reserva: a linha é inserida (e commitada) ANTES de chamar
# a Meta, para que o UNIQUE em comment_id trave um segundo worker processando o
# mesmo comentário. Nunca é um desfecho — vira enviado/falhou logo em seguida.
# Se ficar preso assim, houve queda do worker no meio do envio.
DM_PROCESSANDO = "processando"


class InstagramConnection(Base):
    """Conta profissional do Instagram conectada por uma aluna. Uma por usuário."""

    __tablename__ = "instagram_connections"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )

    ig_user_id = Column(String(64), nullable=False, index=True)
    ig_username = Column(String(255), nullable=True)
    ig_avatar_url = Column(Text, nullable=True)

    # Token longo (60 dias) criptografado com Fernet (SHOPEE_ENCRYPTION_KEY).
    access_token = Column(Text, nullable=False)
    token_expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(Text, nullable=True)

    status = Column(String(16), nullable=False, default=CONEXAO_ATIVA)

    # A conta está inscrita para receber webhook de comentário?
    # Assinar `comments` no painel é do APP; a CONTA precisa de uma chamada própria.
    webhook_subscrito = Column(Boolean, nullable=False, default=False)
    webhook_subscrito_em = Column(DateTime(timezone=True), nullable=True)
    webhook_erro = Column(Text, nullable=True)

    connected_at = Column(DateTime(timezone=True), server_default=func.now())
    last_refreshed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    automations = relationship(
        "InstagramAutomation", back_populates="connection", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("user_id", name="uq_instagram_connection_user"),)


class InstagramAutomation(Base):
    """Uma regra: em quais posts, com quais palavras, o que responder e o que mandar."""

    __tablename__ = "instagram_automations"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id = Column(
        Integer, ForeignKey("instagram_connections.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome = Column(String(255), nullable=False)

    escopo = Column(String(24), nullable=False, default=ESCOPO_POST_ESPECIFICO)
    media_id = Column(String(64), nullable=True)
    media_thumbnail_url = Column(Text, nullable=True)
    media_caption_preview = Column(Text, nullable=True)
    media_permalink = Column(Text, nullable=True)

    trigger_tipo = Column(String(16), nullable=False, default=TRIGGER_PALAVRAS)
    # Já normalizadas (minúsculas, sem acento/emoji) — o matching roda por
    # comentário e não pode pagar normalização da configuração toda vez.
    palavras = Column(JsonCol, nullable=False, default=list)
    # Texto original digitado pela aluna, que é o que a tela exibe.
    palavras_exibicao = Column(JsonCol, nullable=False, default=list)

    resposta_publica_ativa = Column(Boolean, nullable=False, default=True)
    resposta_publica_variacoes = Column(JsonCol, nullable=False, default=list)
    # Rotação persistida: repetir o mesmo texto em 100 comentários é o que faz o
    # Instagram tratar a conta como bot, e um contador em memória zeraria a cada
    # reinício de worker.
    resposta_publica_indice = Column(Integer, nullable=False, default=0)

    dm_texto = Column(Text, nullable=False, default="")

    status = Column(String(16), nullable=False, default=AUTOMACAO_RASCUNHO)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    connection = relationship("InstagramConnection", back_populates="automations")

    __table_args__ = (
        Index("idx_instagram_automations_conn_status", "connection_id", "status"),
        Index("idx_instagram_automations_media", "connection_id", "media_id"),
    )

    def cobre_media(self, media_id: str) -> bool:
        """A automação vale para este post?

        `qualquer` cobre tudo. `proximo` só cobre depois de amarrado a um post
        concreto — enquanto `media_id` for NULL ele ainda está esperando a
        próxima publicação e não deve responder a post nenhum.
        """
        if self.escopo == ESCOPO_QUALQUER:
            return True
        return bool(self.media_id) and str(self.media_id) == str(media_id)


class InstagramEvent(Base):
    """Um comentário processado. `comment_id` é UNIQUE — é a trava de duplicidade."""

    __tablename__ = "instagram_events"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    automation_id = Column(
        Integer, ForeignKey("instagram_automations.id", ondelete="CASCADE"), nullable=True, index=True
    )

    comment_id = Column(String(64), nullable=False)
    media_id = Column(String(64), nullable=True)
    commenter_id = Column(String(64), nullable=True)
    commenter_username = Column(String(255), nullable=True)
    comment_text = Column(Text, nullable=True)
    comment_timestamp = Column(DateTime(timezone=True), nullable=True)

    dm_status = Column(String(16), nullable=False, default=DM_ENVIADO)
    dm_message_id = Column(String(128), nullable=True)
    reply_status = Column(String(16), nullable=True)
    erro_codigo = Column(String(32), nullable=True)
    erro_mensagem = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("comment_id", name="uq_instagram_event_comment"),
        Index("idx_instagram_events_dedupe", "automation_id", "media_id", "commenter_id"),
        Index("idx_instagram_events_user_processed", "user_id", "processed_at"),
    )
