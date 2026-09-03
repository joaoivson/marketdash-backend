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

CONTEUDO_TEXTO = "texto"
CONTEUDO_MIDIA = "midia"
CONTEUDO_OFERTA = "oferta"
CONTEUDO_ACAO = "acao_grupo"

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
    offset_minutos = Column(Integer, nullable=True)
    tipo_conteudo = Column(String(16), nullable=False)
    texto = Column(Text, nullable=True)
    midia_url = Column(Text, nullable=True)
    oferta_url = Column(Text, nullable=True)
    template_id = Column(Integer, ForeignKey("templates_mensagem.id", ondelete="SET NULL"),
                         nullable=True)
    acao = Column(String(24), nullable=True)
    acao_parametro = Column(Text, nullable=True)
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
