"""Schemas do monitoramento de grupos — F8."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class MonitoramentoCriar(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    grupo_origem_id: int
    destino_campanha_id: Optional[int] = None
    destino_grupo_ids: Optional[List[int]] = None
    converter_links: bool = True
    somente_com_link: bool = True
    palavras_chave: Optional[List[str]] = None
    replicar_automaticamente: bool = False

    @model_validator(mode="after")
    def _destino_coerente(self):
        """Origem no destino é o monitoramento virando eco de si mesmo — e, se
        o grupo for de terceiro, a afiliada anunciando dentro do grupo dele."""
        if self.destino_grupo_ids and self.grupo_origem_id in self.destino_grupo_ids:
            raise ValueError("O grupo de origem não pode ser destino da replicação.")
        return self


class MonitoramentoAtualizar(BaseModel):
    nome: Optional[str] = Field(default=None, max_length=120)
    destino_campanha_id: Optional[int] = None
    destino_grupo_ids: Optional[List[int]] = None
    ativo: Optional[bool] = None
    converter_links: Optional[bool] = None
    somente_com_link: Optional[bool] = None
    palavras_chave: Optional[List[str]] = None
    replicar_automaticamente: Optional[bool] = None


class MonitoramentoOut(BaseModel):
    id: int
    nome: str
    grupo_origem_id: int
    grupo_origem: Optional[str] = None
    instancia_id: Optional[int] = None
    destino_campanha_id: Optional[int] = None
    destino_grupo_ids: Optional[List[int]] = None
    ativo: bool
    converter_links: bool
    somente_com_link: bool
    palavras_chave: Optional[List[str]] = None
    replicar_automaticamente: bool
    total_capturas: int = 0
    criado_em: datetime


class CapturaOut(BaseModel):
    id: int
    status: str
    motivo: Optional[str] = None
    texto_original: Optional[str] = None
    texto_final: Optional[str] = None
    link_original: Optional[str] = None
    link_convertido: Optional[str] = None
    roteiro_id: Optional[int] = None
    criado_em: datetime
    replicado_em: Optional[datetime] = None


class CapturasOut(BaseModel):
    capturas: List[CapturaOut]
