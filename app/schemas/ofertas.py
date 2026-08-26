"""Schemas de ofertas e integrações de marketplace — F5."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class OfertaOut(BaseModel):
    item_id: str
    nome: str
    imagem_url: Optional[str]
    loja: Optional[str]
    preco: float
    preco_de: Optional[float]
    desconto_pct: float
    comissao_pct: float
    comissao_valor: float
    vendas: int
    avaliacao: Optional[float]
    url: str


class BuscaOfertasOut(BaseModel):
    ofertas: List[OfertaOut]
    pagina: int
    tem_proxima: bool
    total_na_pagina: int
    termo_usado: str
    # True quando não houve termo: é a vitrine que a tela abre por padrão, não
    # um resultado de busca. A tela precisa distinguir para não dizer
    # "nenhum resultado para ''" quando a vitrine vier vazia.
    vitrine: bool = False


class IntegracaoCriar(BaseModel):
    provedor: str = "shopee"
    label: str = Field(default="principal", max_length=64)
    app_id: str = Field(min_length=1, max_length=64)
    senha: str = Field(min_length=1, max_length=255)


class IntegracaoAtualizar(BaseModel):
    ativa: Optional[bool] = None


class IntegracaoOut(BaseModel):
    id: int
    provedor: str
    label: str
    ativa: bool
    app_id_mascarado: str
    criado_em: datetime
