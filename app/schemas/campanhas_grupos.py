"""Schemas das campanhas de grupos — F2 e F7."""
from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class CampanhaCriar(BaseModel):
    # Sem `descricao` (spec §1.1): era campo em branco na primeira interação e
    # o nome já identifica a campanha. A COLUNA continua no banco — o que sai é
    # a exposição, para não exigir migração destrutiva.
    nome: str = Field(min_length=1, max_length=120)


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
    # Teto por campanha (spec §3.4). 1024 é o limite do WhatsApp e continua
    # sendo o teto absoluto; este só aperta. None/0 = apagar o limite.
    limite_participantes: Optional[int] = Field(default=None, ge=0, le=1024)


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
    limite_participantes: Optional[int]
    total_grupos: int
    criado_em: datetime


class GrupoDaCampanhaItem(BaseModel):
    grupo_id: int
    posicao: int = 0
    aberto: bool = True
    # Override de "cheio" (080). `None` = sem override, vale a ocupação.
    # Precisa ser explicitamente enviado para ser gravado — quem manda a tupla
    # sem ele não mexe no que já estava.
    cheio_override: Optional[bool] = None


class GrupoDaCampanhaOut(BaseModel):
    grupo_id: int
    posicao: int
    # Decisão da usuária: participa da rotação de entrada?
    aberto: bool
    # Estado EFETIVO de lotação — `cheio_override` quando existe, senão a
    # ocupação contra o teto. É este que a tela mostra no select Sim/Não.
    cheio: bool
    # O override cru, para a tela saber se o valor é dela ou do sistema.
    cheio_override: Optional[bool] = None
    # Teto efetivo (o menor entre capacidade e limite da campanha) — a tela
    # mostra "946/900" sem repetir a regra em JavaScript.
    teto: int
    nome: Optional[str]
    participantes: int
    # Capacidade real do WhatsApp — a tela mostra ocupação (951/900) usando o
    # MENOR entre ela e o limite da campanha (spec §3.5).
    capacidade: int
    permite_envio: bool
    ativo: bool
    sub_id: Optional[str]
    # Por quais números este grupo é alcançável. A aba Grupos precisa disso
    # para não oferecer grupo de número que a campanha não usa (spec §2.3).
    instancia_ids: List[int] = []


class CampanhaDetalheOut(CampanhaOut):
    grupos: List[GrupoDaCampanhaOut]
    # Números que a campanha usa — a aba Grupos escopa a oferta por eles, e
    # sem nenhum selecionado ela mostra o estado que aponta para Números.
    instancia_ids: List[int] = []


# --- Números da campanha (spec §2) ------------------------------------------


class NumeroDaCampanhaOut(BaseModel):
    id: int
    nome_exibicao: Optional[str]
    # Já mascarado pelo backend: a tela não precisa do número inteiro para a
    # afiliada reconhecer qual chip é.
    numero: Optional[str]
    status: str
    selecionado: bool
    # Quantos grupos DESTA campanha chegam por este número — é o que explica
    # por que remover está bloqueado.
    grupos_na_campanha: int


class NumerosDaCampanhaOut(BaseModel):
    numeros: List[NumeroDaCampanhaOut]


# --- Visão geral (spec §1.3) ------------------------------------------------


class PontoDaSerieOut(BaseModel):
    data: str
    entradas: int
    saidas: int
    # O dia corrente ainda não fechou. A tela desenha esse ponto diferente
    # (tracejado + rótulo) para não ser lido como queda.
    parcial: bool = False


class EstadoDosGruposOut(BaseModel):
    total: int
    abertos: int
    cheios: int
    disponiveis: int


class VisaoGeralOut(BaseModel):
    """
    KPIs operacionais + ritmo. Sem comissão, lucro ou ROAS — isso é Resultados.

    `taxa_entrada` e `evasao` vêm `None` quando o denominador não existe: 0,0
    afirmaria "ninguém converteu", que é outra coisa.
    """

    periodo: Dict[str, object]
    cliques: int
    entradas: int
    entradas_do_link: int
    taxa_entrada: Optional[float]
    saidas: int
    evasao: Optional[float]
    participantes: int
    grupos: EstadoDosGruposOut
    serie: List[PontoDaSerieOut]


# --- F7: anúncios × grupos e resultados -------------------------------------


class VinculoDeAnuncioOut(BaseModel):
    id: int
    nome: str


class AnuncioVinculavelOut(BaseModel):
    id: int
    nome: Optional[str]
    status: Optional[str]
    sub_id: Optional[str]
    # Gasto no período consultado. Existe porque há campanhas com nome
    # IDÊNTICO no Meta — sem o gasto elas são indistinguíveis na hora de
    # escolher qual vincular (spec §4.4).
    gasto: float = 0.0
    # Veiculação REAL, não `effective_status`: campanha com orçamento vitalício
    # esgotado fica ACTIVE para sempre na Meta sem entregar nada (spec §4.2).
    veiculando: bool = False
    vinculada: bool
    # Vinculada a OUTRA campanha de grupos: a tela desabilita em vez de deixar
    # a afiliada clicar e tomar 409. Traz id além do nome para o link levar
    # direto à campanha que a detém.
    vinculada_em_outra: Optional[VinculoDeAnuncioOut] = None


class AnunciosDaCampanhaOut(BaseModel):
    anuncios: List[AnuncioVinculavelOut]


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
    # Sem `gasto_atribuido`, `lucro` e `lucro_por_pessoa` (04/09). Os três
    # dependiam de ratear o gasto da campanha entre os grupos, e não existe
    # informação para essa divisão: o rateio caía em "partes iguais" sempre que
    # ninguém entrava no período, ignorando tamanho, data de entrada e volume.
    # Gasto, lucro e ROAS agora só existem no nível da CAMPANHA, em `totais`.
    # A comissão por grupo continua real — vem do Sub ID do grupo, rastreada.


class TotaisDeResultadoOut(BaseModel):
    participantes: int
    entradas: int
    saidas: int
    ficaram: int
    mensagens: int
    cliques: int
    pedidos: int
    comissao_liquida: float
    # O investimento INTEIRO da campanha, uma vez — não a soma de um rateio.
    gasto_atribuido: float
    lucro: float
    # None sem investimento: ROAS não existe, e 0.00x afirmaria que cada real
    # gasto voltou zero.
    roas: Optional[float] = None
    # None quando não há participante: a métrica não existe, e 0,00 diria
    # "cada pessoa rende zero", que é outra afirmação.
    lucro_por_pessoa: Optional[float] = None


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
    # None quando não há participante: a métrica não existe, e 0,00 diria
    # "cada pessoa rende zero", que é outra afirmação.
    lucro_por_pessoa: Optional[float] = None


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


# --- Sub IDs da campanha (080) ----------------------------------------------


class SubIdVinculavelOut(BaseModel):
    """Uma opção da lista de "Vincular Sub ID" da campanha de grupos."""

    sub_id: str
    pedidos: int
    comissao_liquida: float
    vinculado: bool
    # Preenchido quando o Sub ID NÃO pode ser vinculado, com o motivo pronto
    # para a tela ("já entra pelo grupo X", "vinculado ao anúncio Y").
    bloqueado_por: Optional[str] = None


class SubIdsDaCampanhaOut(BaseModel):
    sub_ids: List[SubIdVinculavelOut]
