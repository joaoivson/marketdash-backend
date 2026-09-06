"""Schemas dos roteiros e do envio rápido — F3."""
from datetime import date, datetime, time
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from app.models.roteiro import (
    ACOES_DESCONTINUADAS, ACOES_VALIDAS, ACAO_IMAGEM, ACAO_RENOMEAR,
    BLOCOS_ENVIAVEIS, CONTEUDO_ACAO, CONTEUDO_MENSAGEM,
)

#: Nomes que a afiliada lê no erro — a lista de ações é curta e o texto cru
#: ("renomear_grupo") não diz nada para quem está montando um lançamento.
ROTULO_DA_ACAO = {
    "renomear_grupo": "Renomear o grupo",
    "alterar_descricao": "Alterar a descrição",
    "alterar_imagem": "Alterar a imagem",
}


class BlocoIn(BaseModel):
    """Um pedaço do que sai no passo. Um envio real é frequentemente 4 imagens
    + um texto — e isso é diferente de 4 passos com `+0s`."""
    tipo: Literal["texto", "imagem", "audio", "video", "oferta"]
    conteudo: Optional[str] = Field(default=None, max_length=4000)
    legenda: Optional[str] = Field(default=None, max_length=4000)
    template_id: Optional[int] = None

    @model_validator(mode="after")
    def _conteudo_coerente(self):
        if self.tipo not in BLOCOS_ENVIAVEIS:
            # `audio`/`video`/`oferta` existem no schema para quando a fila de
            # ofertas for definida. Aceitar aqui e falhar no disparo viraria
            # uma linha `pulado` com motivo críptico horas depois.
            raise ValueError(
                "Por enquanto o passo aceita blocos de texto e de imagem."
            )
        if not (self.conteudo or "").strip():
            faltando = "a imagem" if self.tipo == "imagem" else "o texto"
            raise ValueError(f"Um bloco de {self.tipo} precisa d{faltando}.")
        return self


class BlocoOut(BaseModel):
    """Leitura, **sem herdar o validator de escrita**.

    `BlocoOut(BlocoIn)` levava junto `_conteudo_coerente`, que exige conteúdo
    não vazio — e a linha vinda do banco pode ter `conteudo` NULL (a 082
    converte passo de texto sem texto). `GET /roteiros/{id}` respondia **500**
    ao montar a resposta de um roteiro perfeitamente salvável.
    """
    id: int
    ordem: int
    tipo: str
    conteudo: Optional[str] = None
    legenda: Optional[str] = None
    template_id: Optional[int] = None


class PassoIn(BaseModel):
    #: **O id é o que mantém a fila viva.** O PUT manda a lista completa; sem o
    #: id, salvar significava apagar e recriar os passos — e o CASCADE de
    #: `roteiro_mensagens.passo_id` levava junto tudo que ainda não tinha saído.
    id: Optional[int] = None
    ordem: int = 0
    tipo_tempo: Literal["ancora", "relativo"] = "ancora"
    hora_fixa: Optional[time] = None
    data_fixa: Optional[date] = None
    offset_valor: Optional[int] = Field(default=None, ge=0, le=100_000)
    offset_unidade: Optional[Literal["segundos", "minutos", "horas"]] = None
    tipo_conteudo: Literal["mensagem", "oferta", "acao_grupo"]
    blocos: List[BlocoIn] = Field(default_factory=list)
    texto: Optional[str] = Field(default=None, max_length=4000)
    midia_url: Optional[str] = None
    oferta_url: Optional[str] = None
    template_id: Optional[int] = None
    acao: Optional[str] = None
    acao_parametro: Optional[str] = None
    grupos_alvo: Literal["todos", "selecao"] = "todos"
    grupos_alvo_ids: Optional[List[int]] = None
    marcar_todos: Literal["nunca", "sempre"] = "nunca"

    @model_validator(mode="after")
    def _tempo_coerente(self):
        """Hora fixa exige data E hora.

        "Data própria" era opcional só porque a data-âncora global preenchia a
        lacuna. A âncora saiu (abertura de carrinho e virada de lote são data e
        hora absolutas), então sem data o passo não tem quando.
        """
        if self.tipo_tempo == "ancora":
            if self.hora_fixa is None:
                raise ValueError("Escolha o horário deste passo.")
            if self.data_fixa is None:
                raise ValueError("Escolha a data deste passo.")
        else:
            if self.offset_valor is None:
                raise ValueError("Diga quanto tempo depois do passo anterior.")
            if not self.offset_unidade:
                self.offset_unidade = "minutos"
        return self

    @model_validator(mode="after")
    def _conteudo_coerente(self):
        """Conteúdo inválido tem que falhar ao SALVAR, não no disparo — lá vira
        uma linha `pulado` com motivo críptico horas depois."""
        if self.tipo_conteudo == CONTEUDO_MENSAGEM:
            if not self.blocos:
                raise ValueError("Adicione ao menos uma mensagem a este passo.")
            if self.acao:
                raise ValueError("Passo de mensagem não tem ação no grupo.")
            return self

        # Ação é EXCLUSIVA: uma por passo, sem blocos.
        if self.blocos:
            raise ValueError(
                "Passo de ação no grupo não aceita mensagens — use um passo separado."
            )
        if self.tipo_conteudo == "oferta":
            if not (self.oferta_url or "").strip():
                raise ValueError("Informe o link da oferta.")
            if self.acao:
                # O `return self` daqui pulava a checagem de `acao` abaixo: um
                # passo de oferta aceitava e gravava qualquer ação, que o motor
                # nunca executaria (só `acao_grupo` chega em `_executar_acao`).
                raise ValueError("Passo de oferta não tem ação no grupo.")
            return self

        if self.acao in ACOES_DESCONTINUADAS:
            raise ValueError(
                "Abrir e fechar entrada saíram do roteiro — use o botão "
                "\"Aberto\" na aba Grupos."
            )
        if self.acao not in ACOES_VALIDAS:
            disponiveis = " · ".join(ROTULO_DA_ACAO[a] for a in ACOES_VALIDAS)
            raise ValueError(f"Escolha a ação: {disponiveis}")
        if self.acao in (ACAO_RENOMEAR, "alterar_descricao") and not (
                self.acao_parametro or "").strip():
            alvo = "o novo nome" if self.acao == ACAO_RENOMEAR else "a nova descrição"
            raise ValueError(f"Informe {alvo} do grupo.")
        if self.acao == ACAO_IMAGEM and not (self.acao_parametro or "").strip():
            raise ValueError("Envie a nova imagem do grupo.")
        return self

    @model_validator(mode="after")
    def _alvo_coerente(self):
        if self.grupos_alvo == "selecao" and not (self.grupos_alvo_ids or []):
            raise ValueError("Escolha ao menos um grupo.")
        return self


class RoteiroCriar(BaseModel):
    nome: str = Field(min_length=1, max_length=120)
    campanha_id: Optional[int] = None
    passos: List[PassoIn] = Field(default_factory=list)


class GrupoComFalha(BaseModel):
    grupo_id: int
    nome: str
    motivo: Optional[str] = None


class StatusDoPasso(BaseModel):
    """Status da ÚLTIMA execução, não do passo. O roteiro é template: o mesmo
    vai rodar no próximo lançamento."""
    status: Literal["concluido", "concluido_com_falhas", "falhou"]
    enviados: int
    pendentes: int
    falhas: List[GrupoComFalha] = []


class PassoOut(BaseModel):
    id: int
    ordem: int
    tipo_tempo: str
    hora_fixa: Optional[time]
    data_fixa: Optional[date]
    offset_valor: Optional[int]
    offset_unidade: Optional[str]
    tipo_conteudo: str
    blocos: List[BlocoOut] = []
    texto: Optional[str]
    midia_url: Optional[str]
    oferta_url: Optional[str]
    template_id: Optional[int]
    acao: Optional[str]
    acao_parametro: Optional[str]
    acao_descontinuada: bool = False
    grupos_alvo: str
    grupos_alvo_ids: Optional[List[int]]
    marcar_todos: str
    #: Horário resolvido em BRT — a linha do passo mostra "07/09, 12:10", não só
    #: "+5 min". Sem isso ela ancora um `+5min` num passo de ontem e não vê que
    #: caiu no passado.
    quando: Optional[datetime] = None
    no_passado: bool = False
    #: Já saiu (ou está saindo): não dá para editar, mover nem excluir.
    travado: bool = False
    status: Optional[StatusDoPasso] = None


class ExecucaoResumo(BaseModel):
    id: int
    status: str
    total: int
    enviados: int
    erros: int
    pulados: int
    proxima_execucao_em: Optional[datetime]
    concluido_em: Optional[datetime]


class RoteiroOut(BaseModel):
    id: int
    nome: str
    campanha_id: Optional[int]
    status: str
    origem: str
    total_passos: int
    criado_em: datetime
    #: Chip da listagem e botão "Agendar": enquanto houver execução ativa, o
    #: roteiro não é "Rascunho" e não pode ser agendado de novo.
    execucao_ativa: Optional[ExecucaoResumo] = None
    ultima_execucao: Optional[ExecucaoResumo] = None


class RoteiroDetalheOut(RoteiroOut):
    passos: List[PassoOut]
    avisos: List[str] = []
    passos_no_passado: List[int] = []


class AgendarIn(BaseModel):
    #: Legado do modelo de âncora global: cada passo carrega a própria data
    #: desde a 082. Continua aceito para não quebrar cliente antigo, e serve de
    #: rede para passo pré-082 que ficou sem `data_fixa`.
    data_ancora: Optional[date] = None
    ignorar_avisos: bool = False


class AjusteDeData(BaseModel):
    passo_id: int
    data_fixa: date
    hora_fixa: Optional[time] = None


class AjustarDatasIn(BaseModel):
    """Ajuste em bloco depois de duplicar: abrir modal por modal em 22 passos é
    onde o erro acontece."""
    datas: List[AjusteDeData] = Field(min_length=1)


class ReenviarIn(BaseModel):
    passo_id: int
    grupo_ids: List[int] = Field(min_length=1)


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
    passos: Dict[int, StatusDoPasso] = {}
