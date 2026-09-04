"""Números conectados (sessões WAHA) e grupos da afiliada — Módulo de Grupos F1.

Espelho da migration 058. As tabelas do resumo diário (whatsapp_optins/envios)
vivem em app/models/whatsapp.py e não mudam.
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.base import Base

INSTANCIA_CRIADA = "criada"
INSTANCIA_CONECTADA = "conectada"
INSTANCIA_DESCONECTADA = "desconectada"
INSTANCIA_REMOVIDA = "removida"


class WhatsappInstancia(Base):
    __tablename__ = "whatsapp_instancias"

    id = Column(Integer, primary_key=True, index=True)
    # SEM unique: multi-número desde o v1 (spec §4.2).
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    nome_exibicao = Column(String(120), nullable=True)
    # Chave de roteamento do webhook: mkd{ref4}u{user_id}x{hex4}.
    nome_instancia = Column(String(64), nullable=False, unique=True)
    numero = Column(String(32), nullable=True)
    status = Column(String(16), nullable=False, default=INSTANCIA_CRIADA)
    teto_diario = Column(Integer, nullable=True)   # NULL = default do sistema
    falhas_seguidas = Column(Integer, nullable=False, default=0)
    # --- pausa de envio (migration 070). Eixo SEPARADO de `status`: aquele é a
    # saúde da conexão e o webhook do WAHA o sobrescreve a cada evento; este é a
    # intenção da afiliada. Um chip pode estar conectado E pausado.
    envio_pausado = Column(Boolean, nullable=False, default=False)
    pausado_em = Column(DateTime(timezone=True), nullable=True)
    ultima_conexao_em = Column(DateTime(timezone=True), nullable=True)
    # --- proxy por sessão (migration 068). STICKY: o chip fica com um IP fixo
    # enquanto estiver saudável — trocar de IP é o que denuncia automação.
    # `proxy_trocas` existe para que a troca seja um evento medido, não rotina.
    proxy_id = Column(Integer, ForeignKey("whatsapp_proxies.id", ondelete="SET NULL"),
                      nullable=True, index=True)
    proxy_fixado_em = Column(DateTime(timezone=True), nullable=True)
    proxy_trocas = Column(Integer, nullable=False, default=0)
    # --- servidor WAHA (migration 071). DEFINITIVO: o estado do whatsmeow vive
    # no Postgres do WAHA que pareou a sessão, então mudar isto depois não move
    # a sessão — só faz o backend falar com a caixa errada. Nulo = sessão
    # anterior ao pool; o resolvedor cai em settings.WAHA_URL.
    servidor_id = Column(Integer, ForeignKey("waha_servidores.id", ondelete="SET NULL"),
                         nullable=True, index=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WhatsappGrupo(Base):
    __tablename__ = "whatsapp_grupos"
    # Espelha a constraint da migration 058: se o create_all chegar antes dela
    # (a corrida que o protocolo tenta impedir), a tabela ainda nasce com o
    # guard que impede grupo duplicado com dois sub_ids.
    __table_args__ = (UniqueConstraint("user_id", "jid", name="uq_whatsapp_grupos_user_jid"),)

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    jid = Column(String(64), nullable=False)
    nome = Column(String(255), nullable=True)
    foto_url = Column(Text, nullable=True)
    # AGREGADO (contagem) — a lista de membros nunca toca o banco (LGPD).
    participantes = Column(Integer, nullable=False, default=0)
    capacidade = Column(Integer, nullable=False, default=1024)
    sou_admin = Column(Boolean, nullable=False, default=False)
    permite_envio = Column(Boolean, nullable=False, default=False)
    link_convite = Column(Text, nullable=True)
    categoria = Column(String(64), nullable=True)
    # False quando some do sync — NUNCA deletar (atribuição histórica).
    ativo = Column(Boolean, nullable=False, default=True)
    # Toggle "Ativo" da USUÁRIA (migration 074, spec §6.2/6.3). Eixo SEPARADO
    # de `ativo`: aquele é lifecycle do sync (todo sync revive com ativo=True),
    # e gravar a escolha manual lá faria a madrugada desfazê-la. O sync NUNCA
    # escreve aqui; quem escreve é o PATCH /grupos/{id}. Ativar é o ponto de
    # atribuição: garante sub_id + custom_link na hora.
    ativado = Column(Boolean, nullable=False, default=False, server_default="false")
    sub_id = Column(String(24), nullable=True, unique=True)
    custom_link_id = Column(Integer, ForeignKey("custom_links.id", ondelete="SET NULL"),
                            nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class WhatsappGrupoInstancia(Base):
    __tablename__ = "whatsapp_grupo_instancias"

    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      primary_key=True)
    instancia_id = Column(Integer, ForeignKey("whatsapp_instancias.id", ondelete="CASCADE"),
                          primary_key=True)
    sou_admin = Column(Boolean, nullable=False, default=False)


class GrupoParticipante(Base):
    """Quem está no grupo AGORA (espelho da 080).

    **Inverte a decisão de 03/09.** Até aqui a lista de membros era usada em
    memória e descartada, e o banco guardava só a contagem — por isso
    "exportar leads" só conseguia exportar EVENTOS de entrada, e um grupo com
    946 pessoas acumuladas em meses exportava 8 linhas.

    É também o caminho que resolve LID→telefone: o payload REST de
    `/api/{sessao}/groups` traz `PhoneNumber` ao lado do `JID`, enquanto o
    webhook de entrada nem sempre traz — foi por isso que 49 de 49 eventos
    nasceram como `lid`. A política de privacidade precisa refletir isto.

    O sync é a única escrita: faz upsert de quem está e apaga quem sumiu, para
    a tabela nunca virar histórico (quem saiu está em `grupo_eventos`).
    """

    __tablename__ = "grupo_participantes"

    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      primary_key=True)
    # Identidade COMO VEIO do WhatsApp, igual a `grupo_eventos.identificador`:
    # "5511999999999@c.us" ou "84729130@lid".
    identificador = Column(String(64), primary_key=True)
    # O telefone resolvido, quando o WhatsApp o informa. Coluna SEPARADA do
    # identificador porque em grupo LID os dois são valores diferentes — e foi
    # exatamente essa confusão que fez o CSV sair com a coluna vazia.
    telefone = Column(String(32), nullable=True)
    # Mesmo HMAC de `grupo_eventos.identificador_hash`: é por ele que a
    # exportação acha a data de entrada de quem tem evento registrado.
    identificador_hash = Column(String(64), nullable=True, index=True)
    admin = Column(Boolean, nullable=False, default=False)
    # Primeira vez que o sync viu a pessoa. NÃO é a entrada real de quem já
    # estava no grupo antes do módulo existir — por isso a exportação prefere
    # a data do evento quando ela existe.
    visto_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    confirmado_em = Column(DateTime(timezone=True), nullable=False,
                           server_default=func.now())
