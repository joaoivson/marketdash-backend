"""Schemas das campanhas de grupos — F2 e F7."""
from datetime import datetime
from typing import Dict, List, Optional

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


# --- F7: anúncios × grupos e resultados -------------------------------------


class AnuncioVinculavelOut(BaseModel):
    id: int
    nome: Optional[str]
    status: Optional[str]
    sub_id: Optional[str]
    vinculada: bool
    # Vinculada a OUTRA campanha de grupos: a tela desabilita em vez de deixar
    # a afiliada clicar e tomar 409.
    vinculada_em_outra: Optional[str] = None


class AnunciosDaCampanhaOut(BaseModel):
    anuncios: List[AnuncioVinculavelOut]


class VinculoDeAnuncioOut(BaseModel):
    id: int
    nome: str


class VinculosDeAnuncioOut(BaseModel):
    # Chave int no Python; em JSON vira string ("268"), que é como a tela
    # indexa. Declarar `str` aqui faz o response_model recusar o dict inteiro.
    vinculos: Dict[int, VinculoDeAnuncioOut]


class PeriodoOut(BaseModel):
    inicio: str
    fim: str


class LinhaDeResultadoOut(BaseModel):
    grupo_id: int
    grupo: Optional[str]
    sub_id: Optional[str]
    participantes: int
    entradas: int
    saidas: int
    ficaram: int
    evasao_pct: float
    mensagens: int
    cliques: int
    pedidos: int
    comissao_liquida: float
    gasto_atribuido: float
    lucro: float
    lucro_por_pessoa: float


class TotaisDeResultadoOut(BaseModel):
    participantes: int
    entradas: int
    saidas: int
    ficaram: int
    mensagens: int
    cliques: int
    pedidos: int
    comissao_liquida: float
    gasto_atribuido: float
    lucro: float
    lucro_por_pessoa: float


class AnunciosDoPeriodoOut(BaseModel):
    campanhas_vinculadas: int
    investimento: float
    investimento_com_imposto: float
    # None ≠ 0: None = "configure o pixel", 0 = "ninguém virou lead". Colapsar
    # os dois vira ticket de suporte.
    leads: Optional[int] = None
    cpl: Optional[float] = None
    custo_por_entrada: Optional[float] = None
    custo_por_permanencia: Optional[float] = None


class ResultadosOut(BaseModel):
    periodo: PeriodoOut
    linhas: List[LinhaDeResultadoOut]
    totais: TotaisDeResultadoOut
    anuncios: AnunciosDoPeriodoOut


class ResumoDeCampanhaOut(BaseModel):
    campanha_id: int
    nome: str
    grupos: int
    participantes: int
    entradas: int
    comissao_liquida: float
    lucro: float
    lucro_por_pessoa: float


class ResumoConsolidadoOut(BaseModel):
    periodo: PeriodoOut
    campanhas_ativas: int
    # > 0 quando o teto do bloco cortou campanhas: a tela avisa que o total não
    # é de tudo. Corte silencioso lê-se como "somei todas" quando não somou.
    campanhas_omitidas: int = 0
    totais: TotaisDeResultadoOut
    investimento: float
    investimento_com_imposto: float
    leads: Optional[int] = None
    custo_por_entrada: Optional[float] = None
    por_campanha: List[ResumoDeCampanhaOut]
