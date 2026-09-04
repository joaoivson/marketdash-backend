"""Schemas do Módulo de Grupos — F1: números (sessões) e grupos."""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class InstanciaCriar(BaseModel):
    nome_exibicao: Optional[str] = Field(default=None, max_length=120)


class InstanciaAtualizar(BaseModel):
    """PATCH parcial: `None` significa "não mexer neste campo".

    Renomear e pausar chegam pela mesma rota porque são a mesma pergunta da
    tela ("mudar algo neste chip") — e `nome_exibicao` só é validado aqui, já
    que no POST ele é opcional e ganha fallback no service.
    """
    nome_exibicao: Optional[str] = Field(default=None, min_length=1, max_length=120)
    envio_pausado: Optional[bool] = None


class InstanciaOut(BaseModel):
    id: int
    nome_exibicao: Optional[str]
    numero_mascarado: Optional[str]
    status: str
    # Eixo separado de `status`: o chip pode estar conectado E pausado. Quem
    # escreve em `status` é o webhook do WAHA; aqui é a afiliada.
    envio_pausado: bool
    ultima_conexao_em: Optional[datetime]
    criado_em: datetime


class InstanciaQrOut(BaseModel):
    estado: str            # conectada | aguardando | erro: <motivo>
    qrcode: Optional[str]  # data-uri base64, quando há QR a mostrar


class CodigoPareamentoIn(BaseModel):
    """Número que vai receber o código de pareamento (DDI opcional — assume BR)."""
    numero: str


class CodigoPareamentoOut(BaseModel):
    estado: str             # conectada | aguardando | erro: <motivo>
    codigo: Optional[str]   # 8 caracteres para digitar no WhatsApp do celular


class GrupoAtualizar(BaseModel):
    """Só o toggle da usuária — nome/participantes/admin são do sync."""
    ativado: bool


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
    # Toggle da usuária (spec §6.2) — eixo separado do `ativo` do sync.
    ativado: bool
    sub_id: Optional[str]
    instancia_ids: List[int]


class SincronizarOut(BaseModel):
    vistos: int
    novos: int
    atualizados: int
    desativados: int
    # Itens que o WAHA devolveu e não reconhecemos como grupo. Fica na resposta
    # de propósito: foi a métrica ausente que deixou o sync "com sucesso e zero
    # grupos" por dias, em 26/08.
    ignorados: int = 0
    # Convites resolvidos nesta rodada (o resto vai no próximo sync).
    convites: int = 0


# --- item 18: link de conexão externa ----------------------------------------


class ConviteOut(BaseModel):
    id: int
    url: str
    expira_em: datetime


class ConviteAtivoOut(BaseModel):
    """Sem a URL: o token em claro só existe no momento em que é criado. Depois
    disso nem nós conseguimos remontá-lo — o banco guarda só o hash."""
    id: int
    expira_em: datetime
    criado_em: datetime
