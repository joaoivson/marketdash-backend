"""Contratos das rotas do resumo no WhatsApp."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class OptinRequest(BaseModel):
    # Validação de formato fica no normalizar_numero: a regra de celular
    # brasileiro (DDD, nono dígito) não cabe num min/max de string.
    numero: str = Field(min_length=8, max_length=24)


class ConfirmarRequest(BaseModel):
    codigo: str = Field(min_length=4, max_length=8)


class StatusResponse(BaseModel):
    status: str                      # pendente|confirmado|desligado
    numero: Optional[str] = None     # mascarado — o número inteiro nunca volta
    disponivel: bool = False         # False quando a Evolution não está configurada
    confirmado_em: Optional[datetime] = None


class InstanciaResponse(BaseModel):
    """Estado do número do MarketDash, para a tela do admin."""
    configurado: bool
    estado: str
    qrcode: Optional[str] = None     # data URI, presente só quando desconectado
