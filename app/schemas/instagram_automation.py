"""Schemas da automação de Instagram (comentário → direct)."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    AUTOMACAO_PAUSADA,
    AUTOMACAO_RASCUNHO,
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_QUALQUER,
    ESCOPO_STORY_ESPECIFICO,
    ESCOPO_STORY_QUALQUER,
    TRIGGER_PALAVRAS,
    TRIGGER_QUALQUER,
)

ESCOPOS_VALIDOS = {
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_QUALQUER,
    ESCOPO_STORY_ESPECIFICO,
    ESCOPO_STORY_QUALQUER,
}
TRIGGERS_VALIDOS = {TRIGGER_PALAVRAS, TRIGGER_QUALQUER}
STATUS_VALIDOS = {AUTOMACAO_ATIVA, AUTOMACAO_PAUSADA, AUTOMACAO_RASCUNHO}

# Limites do spec §5.3: mínimo 3, máximo 5 variações de resposta pública.
MIN_VARIACOES_RECOMENDADO = 3
MAX_VARIACOES = 5


# --------------------------------------------------------------------------- #
#  Conexão                                                                     #
# --------------------------------------------------------------------------- #


class InstagramAuthUrlResponse(BaseModel):
    url: str


class InstagramOAuthCallback(BaseModel):
    code: str
    redirect_uri: Optional[str] = None
    state: Optional[str] = None


class InstagramConnectionResponse(BaseModel):
    id: int
    ig_user_id: str
    ig_username: Optional[str] = None
    ig_avatar_url: Optional[str] = None
    # ativo | expirado | revogado
    status: str
    connected_at: Optional[datetime] = None
    token_expires_at: Optional[datetime] = None

    # Assinar `comments` no painel vale só para o APP; cada CONTA precisa de uma
    # chamada própria. Quando isto é False, o webhook não chega e NADA dispara —
    # sem erro visível em lugar nenhum. Por isso vai para a tela.
    webhook_subscrito: bool = False
    webhook_erro: Optional[str] = None

    # BUSINESS | MEDIA_CREATOR. Só Criador consegue tornar o perfil privado — e
    # perfil privado não recebe webhook de comentário. A tela usa isso para um
    # aviso preventivo, sem bloquear.
    account_type: Optional[str] = None
    # False = conectou sem `instagram_business_manage_comments`: o direct sai, mas
    # a resposta pública no comentário não. Silencioso se ninguém avisar.
    pode_responder_comentario: bool = True

    class Config:
        from_attributes = True


class InstagramMediaItem(BaseModel):
    id: str
    caption_preview: Optional[str] = None
    media_type: Optional[str] = None
    media_product_type: Optional[str] = None
    permalink: Optional[str] = None
    thumbnail_url: Optional[str] = None
    timestamp: Optional[str] = None


class InstagramMediaPage(BaseModel):
    items: List[InstagramMediaItem] = []
    # Cursor da próxima página ("Carregar mais"). None = acabou.
    next_cursor: Optional[str] = None
    # True quando a resposta veio do cache de 15 min, não da Meta.
    from_cache: bool = False


# --------------------------------------------------------------------------- #
#  Automação                                                                   #
# --------------------------------------------------------------------------- #


class InstagramAutomationBase(BaseModel):
    nome: str = Field(min_length=1, max_length=255)
    escopo: str = ESCOPO_POST_ESPECIFICO
    media_id: Optional[str] = None
    media_thumbnail_url: Optional[str] = None
    media_caption_preview: Optional[str] = None
    media_permalink: Optional[str] = None

    trigger_tipo: str = TRIGGER_PALAVRAS
    # Texto como a aluna digitou. A normalização é feita no service.
    palavras: List[str] = []

    resposta_publica_ativa: bool = True
    resposta_publica_variacoes: List[str] = []

    dm_texto: str = ""
    dm_link: Optional[str] = None
    dm_botao_texto: Optional[str] = Field(default=None, max_length=20)

    status: str = AUTOMACAO_RASCUNHO

    @field_validator("escopo")
    @classmethod
    def _escopo_valido(cls, v: str) -> str:
        if v not in ESCOPOS_VALIDOS:
            raise ValueError(f"escopo deve ser um de {sorted(ESCOPOS_VALIDOS)}")
        return v

    @field_validator("trigger_tipo")
    @classmethod
    def _trigger_valido(cls, v: str) -> str:
        if v not in TRIGGERS_VALIDOS:
            raise ValueError(f"trigger_tipo deve ser um de {sorted(TRIGGERS_VALIDOS)}")
        return v

    @field_validator("status")
    @classmethod
    def _status_valido(cls, v: str) -> str:
        if v not in STATUS_VALIDOS:
            raise ValueError(f"status deve ser um de {sorted(STATUS_VALIDOS)}")
        return v

    @field_validator("resposta_publica_variacoes")
    @classmethod
    def _variacoes_no_limite(cls, v: List[str]) -> List[str]:
        limpas = [t.strip() for t in (v or []) if t and t.strip()]
        if len(limpas) > MAX_VARIACOES:
            raise ValueError(f"no máximo {MAX_VARIACOES} variações de resposta pública")
        return limpas

    @field_validator("palavras")
    @classmethod
    def _palavras_limpas(cls, v: List[str]) -> List[str]:
        return [t.strip() for t in (v or []) if t and t.strip()]

    @field_validator("dm_link")
    @classmethod
    def _link_http(cls, v: Optional[str]) -> Optional[str]:
        """Link vazio é NULL; link torto é recusado antes de virar botão morto."""
        link = (v or "").strip()
        if not link:
            return None
        if not link.startswith(("http://", "https://")):
            raise ValueError("o link precisa começar com http:// ou https://")
        return link

    @field_validator("dm_botao_texto")
    @classmethod
    def _botao_limpo(cls, v: Optional[str]) -> Optional[str]:
        texto = (v or "").strip()
        return texto or None


class InstagramAutomationCreate(InstagramAutomationBase):
    pass


class InstagramAutomationUpdate(InstagramAutomationBase):
    pass


class InstagramAutomationStatusUpdate(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def _status_valido(cls, v: str) -> str:
        if v not in STATUS_VALIDOS:
            raise ValueError(f"status deve ser um de {sorted(STATUS_VALIDOS)}")
        return v


class InstagramAutomationResponse(BaseModel):
    id: int
    user_id: int
    connection_id: int
    nome: str
    escopo: str
    media_id: Optional[str] = None
    media_thumbnail_url: Optional[str] = None
    media_caption_preview: Optional[str] = None
    media_permalink: Optional[str] = None
    trigger_tipo: str
    # Devolve o texto ORIGINAL (palavras_exibicao), não o normalizado — a tela
    # precisa mostrar "QUERO", não "quero".
    palavras: List[str] = []
    resposta_publica_ativa: bool
    resposta_publica_variacoes: List[str] = []
    dm_texto: str
    dm_link: Optional[str] = None
    dm_botao_texto: Optional[str] = None
    status: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Contadores para o card da lista.
    comentarios_capturados: int = 0
    directs_enviados: int = 0

    class Config:
        from_attributes = True
