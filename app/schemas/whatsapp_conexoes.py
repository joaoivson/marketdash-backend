"""Schemas do Módulo de Grupos — F1: números (sessões) e grupos."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class InstanciaCriar(BaseModel):
    nome_exibicao: Optional[str] = Field(default=None, max_length=120)


class InstanciaOut(BaseModel):
    id: int
    nome_exibicao: Optional[str]
    numero_mascarado: Optional[str]
    status: str
    ultima_conexao_em: Optional[datetime]
    criado_em: datetime


class InstanciaQrOut(BaseModel):
    estado: str            # conectada | aguardando | erro: <motivo>
    qrcode: Optional[str]  # data-uri base64, quando há QR a mostrar


class GrupoOut(BaseModel):
    id: int
    jid: str
    nome: Optional[str]
    foto_url: Optional[str]
    participantes: int
    capacidade: int
    sou_admin: bool
    permite_envio: bool
    link_convite: Optional[str]
    ativo: bool
    sub_id: Optional[str]
    instancia_ids: List[int]


class SincronizarOut(BaseModel):
    vistos: int
    novos: int
    atualizados: int
    desativados: int
