"""Contratos das rotas do Diagnóstico IA."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GerarDiagnosticoRequest(BaseModel):
    inicio: date
    fim: date


class PerguntaRequest(BaseModel):
    pergunta: str = Field(min_length=1, max_length=1000)


class MensagemResponse(BaseModel):
    id: int
    papel: str
    conteudo: str
    criado_em: Optional[datetime] = None


class DiagnosticoResumo(BaseModel):
    id: int
    periodo_inicio: date
    periodo_fim: date
    status: str
    criado_em: Optional[datetime] = None


class DiagnosticoResponse(BaseModel):
    id: int
    periodo_inicio: date
    periodo_fim: date
    status: str
    erro_mensagem: Optional[str] = None
    relatorio: Optional[Dict[str, Any]] = None
    snapshot: Optional[Dict[str, Any]] = None
    criado_em: Optional[datetime] = None
    mensagens: List[MensagemResponse] = []


class SaldoResponse(BaseModel):
    saldo: int
    cota: int
    custo_geracao: int
    custo_chat: int
    disponivel: bool
