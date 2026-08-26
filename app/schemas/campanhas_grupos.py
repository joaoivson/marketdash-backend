"""Schemas das campanhas de grupos — F2."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class CampanhaCriar(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    descricao: Optional[str] = Field(default=None, max_length=2000)


class CampanhaAtualizar(BaseModel):
    nome: Optional[str] = Field(default=None, max_length=120)
    descricao: Optional[str] = Field(default=None, max_length=2000)
    status: Optional[str] = None                # ativa|pausada|arquivada
    estrategia_entrada: Optional[str] = None    # sequencial|aleatoria
    abertura_automatica: Optional[bool] = None
    reabertura_automatica: Optional[bool] = None
    prefixo: Optional[str] = Field(default=None, max_length=500)
    sufixo: Optional[str] = Field(default=None, max_length=500)
    modo_imagem: Optional[str] = None           # link_preview|imagem_normal


class CampanhaOut(BaseModel):
    id: int
    nome: str
    descricao: Optional[str]
    status: str
    estrategia_entrada: str
    abertura_automatica: bool
    reabertura_automatica: bool
    prefixo: Optional[str]
    sufixo: Optional[str]
    modo_imagem: str
    total_grupos: int
    criado_em: datetime


class GrupoDaCampanhaItem(BaseModel):
    grupo_id: int
    posicao: int = 0
    aberto: bool = True


class GrupoDaCampanhaOut(BaseModel):
    grupo_id: int
    posicao: int
    aberto: bool
    nome: Optional[str]
    participantes: int
    permite_envio: bool
    ativo: bool
    sub_id: Optional[str]


class CampanhaDetalheOut(CampanhaOut):
    grupos: List[GrupoDaCampanhaOut]
