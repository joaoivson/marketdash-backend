"""Schemas dos roteiros e do envio rápido — F3."""
from datetime import date, datetime, time
from typing import List, Optional

from pydantic import BaseModel, Field, model_validator


class PassoIn(BaseModel):
    ordem: int
    tipo_tempo: str = "ancora"              # ancora|relativo
    hora_fixa: Optional[time] = None
    data_fixa: Optional[date] = None
    offset_minutos: Optional[int] = Field(default=None, ge=0, le=60 * 24 * 7)
    tipo_conteudo: str                      # texto|midia|oferta|acao_grupo
    texto: Optional[str] = Field(default=None, max_length=4000)
    midia_url: Optional[str] = None
    oferta_url: Optional[str] = None
    template_id: Optional[int] = None
    acao: Optional[str] = None
    acao_parametro: Optional[str] = None
    grupos_alvo: str = "todos"              # todos|selecao
    grupos_alvo_ids: Optional[List[int]] = None
    marcar_todos: str = "nunca"

    @model_validator(mode="after")
    def _acao_coerente(self):
        """Ação inválida tem que falhar ao SALVAR, não no disparo — lá vira
        uma linha `pulado` com motivo críptico horas depois."""
        if self.tipo_conteudo != "acao_grupo":
            return self
        acoes = {"renomear_grupo", "abrir_entrada", "fechar_entrada"}
        if self.acao not in acoes:
            raise ValueError(f"Ação inválida. Use uma de: {', '.join(sorted(acoes))}")
        if self.acao == "renomear_grupo" and not (self.acao_parametro or "").strip():
            raise ValueError("Renomear grupo exige o novo nome.")
        return self


class RoteiroCriar(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    campanha_id: Optional[int] = None
    passos: List[PassoIn] = Field(default_factory=list)


class RoteiroOut(BaseModel):
    id: int
    nome: str
    campanha_id: Optional[int]
    status: str
    origem: str
    total_passos: int
    criado_em: datetime


class PassoOut(PassoIn):
    id: int


class RoteiroDetalheOut(RoteiroOut):
    passos: List[PassoOut]


class AgendarIn(BaseModel):
    data_ancora: date
    ignorar_avisos: bool = False


class EnvioRapidoIn(BaseModel):
    texto: Optional[str] = Field(default=None, max_length=4000)
    midia_url: Optional[str] = None
    oferta_url: Optional[str] = None
    grupo_ids: List[int] = Field(min_length=1)
    campanha_id: Optional[int] = None
    agendar_para: Optional[datetime] = None   # None = agora


class ExecucaoOut(BaseModel):
    id: int
    roteiro_id: int
    data_ancora: date
    status: str
    total: int
    enviados: int
    erros: int
    pulados: int
    proxima_execucao_em: Optional[datetime]
    iniciado_em: Optional[datetime]
    concluido_em: Optional[datetime]
    duracao_estimada_s: int
    avisos: List[str] = []
