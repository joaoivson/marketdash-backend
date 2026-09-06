"""Roteiros, execuções, mensagens e templates — F3 (espelho da 060).

O coração do módulo: roteiro = sequência de passos (âncora + relativos);
a execução materializa TODAS as mensagens com horário absoluto, e o motor
trabalha linha a linha com claim atômico. Envio rápido = roteiro de 1 passo.
"""
from sqlalchemy import (
    BigInteger, Boolean, Column, Date, DateTime, ForeignKey, Integer,
    String, Text, Time, UniqueConstraint,
)
from app.models.tipos import JSON_PORTATIL
from sqlalchemy.sql import func

from app.db.base import Base

ROTEIRO_RASCUNHO = "rascunho"
ROTEIRO_PRONTO = "pronto"

ORIGEM_EDITOR = "editor"
ORIGEM_ENVIO_RAPIDO = "envio_rapido"

TEMPO_ANCORA = "ancora"
TEMPO_RELATIVO = "relativo"

# Tipo do PASSO. `texto`/`midia` migraram para `mensagem` na 082 e viraram
# tipos de BLOCO — as constantes ficam porque execução antiga ainda as lê.
CONTEUDO_MENSAGEM = "mensagem"
CONTEUDO_OFERTA = "oferta"
CONTEUDO_ACAO = "acao_grupo"
CONTEUDO_TEXTO = "texto"    # legado (pré-082)
CONTEUDO_MIDIA = "midia"    # legado (pré-082)

# Tipo do BLOCO. `audio`/`video`/`oferta` existem no schema para quando a fila
# de ofertas for definida; o motor de hoje envia `texto` e `imagem`.
BLOCO_TEXTO = "texto"
BLOCO_IMAGEM = "imagem"
BLOCO_AUDIO = "audio"
BLOCO_VIDEO = "video"
BLOCO_OFERTA = "oferta"
BLOCOS_ENVIAVEIS = (BLOCO_TEXTO, BLOCO_IMAGEM)

# Ações no grupo. `abrir_entrada`/`fechar_entrada` saíram na 082 (ambiguidade
# com o toggle "Aberto" da aba Grupos e com o link de entrada da campanha):
# passo antigo fica marcado `acao_descontinuada` e é pulado com motivo claro.
ACAO_RENOMEAR = "renomear_grupo"
ACAO_DESCRICAO = "alterar_descricao"
ACAO_IMAGEM = "alterar_imagem"
ACOES_VALIDAS = (ACAO_RENOMEAR, ACAO_DESCRICAO, ACAO_IMAGEM)
ACOES_DESCONTINUADAS = ("abrir_entrada", "fechar_entrada")

# Unidade de exibição do offset — o canônico gravado é sempre em segundos.
UNIDADE_SEGUNDOS = "segundos"
UNIDADE_MINUTOS = "minutos"
UNIDADE_HORAS = "horas"
UNIDADES = {UNIDADE_SEGUNDOS: 1, UNIDADE_MINUTOS: 60, UNIDADE_HORAS: 3600}

# Estados de execução que ocupam o roteiro (índice único uq_roteiro_execucao_ativa).
EXEC_ATIVAS = ("agendada", "enviando", "pausada")

EXEC_AGENDADA = "agendada"
EXEC_ENVIANDO = "enviando"
EXEC_PAUSADA = "pausada"
EXEC_CONCLUIDA = "concluida"
EXEC_CANCELADA = "cancelada"
EXEC_FALHOU = "falhou"

MSG_PENDENTE = "pendente"
MSG_ENVIANDO = "enviando"
MSG_ENVIADA = "enviado"
MSG_FALHOU = "falhou"
MSG_PULADA = "pulado"


class TemplateMensagem(Base):
    __tablename__ = "templates_mensagem"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    nome = Column(String(120), nullable=False)
    tipo = Column(String(12), nullable=False, default="oferta")   # oferta|livre
    ativo = Column(Boolean, nullable=False, default=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class TemplateVariacao(Base):
    __tablename__ = "template_variacoes"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("templates_mensagem.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    corpo = Column(Text, nullable=False)
    peso = Column(Integer, nullable=False, default=1)
    ativa = Column(Boolean, nullable=False, default=True)


class Roteiro(Base):
    __tablename__ = "roteiros"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         nullable=True, index=True)
    nome = Column(String(120), nullable=False)
    status = Column(String(12), nullable=False, default=ROTEIRO_RASCUNHO)
    origem = Column(String(16), nullable=False, default=ORIGEM_EDITOR)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RoteiroPasso(Base):
    __tablename__ = "roteiro_passos"

    id = Column(Integer, primary_key=True, index=True)
    roteiro_id = Column(Integer, ForeignKey("roteiros.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    ordem = Column(Integer, nullable=False)
    tipo_tempo = Column(String(12), nullable=False, default=TEMPO_ANCORA)
    hora_fixa = Column(Time, nullable=True)
    data_fixa = Column(Date, nullable=True)
    offset_segundos = Column(Integer, nullable=True)
    offset_unidade = Column(String(10), nullable=True)
    tipo_conteudo = Column(String(16), nullable=False)
    texto = Column(Text, nullable=True)
    midia_url = Column(Text, nullable=True)
    oferta_url = Column(Text, nullable=True)
    template_id = Column(Integer, ForeignKey("templates_mensagem.id", ondelete="SET NULL"),
                         nullable=True)
    acao = Column(String(24), nullable=True)
    acao_parametro = Column(Text, nullable=True)
    acao_descontinuada = Column(Boolean, nullable=False, default=False)
    grupos_alvo = Column(String(12), nullable=False, default="todos")
    grupos_alvo_ids = Column(JSON_PORTATIL, nullable=True)
    marcar_todos = Column(String(8), nullable=False, default="nunca")
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RoteiroExecucao(Base):
    __tablename__ = "roteiro_execucoes"

    id = Column(Integer, primary_key=True, index=True)
    roteiro_id = Column(Integer, ForeignKey("roteiros.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    data_ancora = Column(Date, nullable=False)
    status = Column(String(12), nullable=False, default=EXEC_AGENDADA)
    proxima_execucao_em = Column(DateTime(timezone=True), nullable=True)
    total = Column(Integer, nullable=False, default=0)
    enviados = Column(Integer, nullable=False, default=0)
    erros = Column(Integer, nullable=False, default=0)
    pulados = Column(Integer, nullable=False, default=0)
    iniciado_em = Column(DateTime(timezone=True), nullable=True)
    concluido_em = Column(DateTime(timezone=True), nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class RoteiroMensagem(Base):
    __tablename__ = "roteiro_mensagens"
    __table_args__ = (
        UniqueConstraint("execucao_id", "passo_id", "grupo_id", name="uq_roteiro_msg"),
    )

    id = Column(BigInteger, primary_key=True, index=True)
    execucao_id = Column(Integer, ForeignKey("roteiro_execucoes.id", ondelete="CASCADE"),
                         nullable=False, index=True)
    passo_id = Column(Integer, ForeignKey("roteiro_passos.id", ondelete="CASCADE"),
                      nullable=False)
    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      nullable=False)
    instancia_id = Column(Integer, ForeignKey("whatsapp_instancias.id", ondelete="SET NULL"),
                          nullable=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    agendado_para = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(12), nullable=False, default=MSG_PENDENTE)
    short_link = Column(Text, nullable=True)
    texto_final = Column(Text, nullable=True)
    erro_motivo = Column(Text, nullable=True)
    enviado_em = Column(DateTime(timezone=True), nullable=True)
    # Retomada por bloco: falhar no bloco 3 e reenviar do zero mandaria os
    # blocos 1 e 2 DE NOVO no grupo.
    blocos_enviados = Column(Integer, nullable=False, default=0)


class PassoBloco(Base):
    """Um bloco do passo — o que sai, em sequência.

    O passo continua dono do QUANDO, do PARA QUEM e do marcar-todos; o bloco é
    só o conteúdo. Diferente de N passos com `+0s`: blocos compartilham horário
    e grupos, são editados e pré-visualizados juntos, e movem juntos na ordem.
    """
    __tablename__ = "passo_blocos"

    id = Column(Integer, primary_key=True, index=True)
    passo_id = Column(Integer, ForeignKey("roteiro_passos.id", ondelete="CASCADE"),
                      nullable=False, index=True)
    ordem = Column(Integer, nullable=False)
    tipo = Column(String(12), nullable=False)      # texto|imagem|audio|video|oferta
    conteudo = Column(Text, nullable=True)         # texto do bloco, ou URL da mídia
    legenda = Column(Text, nullable=True)          # legenda que acompanha a mídia
    template_id = Column(Integer, ForeignKey("templates_mensagem.id", ondelete="SET NULL"),
                         nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
