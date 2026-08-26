"""Schemas de templates de mensagem e da IA de variações — F4."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class TemplateCriar(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    tipo: str = "oferta"          # oferta|livre


class TemplateAtualizar(BaseModel):
    nome: Optional[str] = Field(default=None, max_length=120)
    tipo: Optional[str] = None
    ativo: Optional[bool] = None


class VariacaoIn(BaseModel):
    corpo: str = Field(min_length=1, max_length=4000)
    peso: int = Field(default=1, ge=1, le=100)
    ativa: bool = True


class VariacaoOut(VariacaoIn):
    id: int


class TemplateOut(BaseModel):
    id: int
    nome: str
    tipo: str
    ativo: bool
    total_variacoes: int
    criado_em: datetime


class TemplateDetalheOut(TemplateOut):
    variacoes: List[VariacaoOut]


class GerarVariacoesIn(BaseModel):
    texto_base: str = Field(min_length=10, max_length=4000)
    # Só estilos do catálogo: o valor entra no prompt, e texto livre aqui é
    # prompt injection paga com a chave da empresa.
    estilo: Optional[str] = Field(default=None, max_length=40)
    quantidade: int = Field(default=3, ge=1, le=10)
    salvar: bool = False          # True = já acrescenta ao template

    @field_validator("estilo")
    @classmethod
    def _estilo_conhecido(cls, v):
        if v is None:
            return v
        from app.services.template_ia_service import ESTILOS

        if v not in ESTILOS:
            raise ValueError(f"Estilo inválido. Use um de: {', '.join(ESTILOS)}")
        return v


class GerarVariacoesOut(BaseModel):
    variacoes: List[str]
    salvas: int = 0
