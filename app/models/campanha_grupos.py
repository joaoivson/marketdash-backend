"""Campanhas de grupos de WhatsApp — Módulo de Grupos F2 (espelho da 059).

"Campanha" aqui é o conjunto de grupos com link de entrada, roteiros e
métricas (spec §2) — NÃO confundir com app/models/campaign.py, que é a
campanha de TRÁFEGO PAGO do Meta (a tela que virou "Anúncios").
"""
from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer, String, Text,
)
from sqlalchemy.sql import func

from app.db.base import Base

CAMPANHA_ATIVA = "ativa"
CAMPANHA_PAUSADA = "pausada"
CAMPANHA_ARQUIVADA = "arquivada"
# Estado TERMINAL do "excluir" da listagem (080). É soft-delete de propósito:
# hard-delete levaria `campanha_links` no CASCADE, o slug deixaria de existir e
# `/g/{slug}` só poderia responder 404 — enquanto o anúncio já veiculando
# continua mandando tráfego por dias. Com a linha viva, o link responde 200 com
# "campanha encerrada". Também preserva a atribuição de gasto
# (`campanha_anuncios`) e os cliques do link, que são histórico financeiro.
CAMPANHA_ENCERRADA = "encerrada"

ESTRATEGIA_SEQUENCIAL = "sequencial"
ESTRATEGIA_ALEATORIA = "aleatoria"

MODO_IMAGEM_LINK = "link_preview"
MODO_IMAGEM_NORMAL = "imagem_normal"


class Campanha(Base):
    __tablename__ = "campanhas"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    nome = Column(String(120), nullable=False)
    descricao = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default=CAMPANHA_ATIVA)
    estrategia_entrada = Column(String(16), nullable=False, default=ESTRATEGIA_SEQUENCIAL)
    abertura_automatica = Column(Boolean, nullable=False, default=True)
    reabertura_automatica = Column(Boolean, nullable=False, default=True)
    # Teto de participantes POR CAMPANHA, abaixo da `capacidade` do grupo.
    # NULL = sem limite próprio (vale a capacidade). Não é 1024 por default
    # porque "não configurado" precisa ser distinguível de "configurado no
    # máximo": só o NULL acompanha uma capacidade que mude no futuro.
    limite_participantes = Column(Integer, nullable=True)
    prefixo = Column(Text, nullable=True)
    sufixo = Column(Text, nullable=True)
    modo_imagem = Column(String(16), nullable=False, default=MODO_IMAGEM_LINK)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CampanhaGrupo(Base):
    __tablename__ = "campanha_grupos"

    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         primary_key=True)
    grupo_id = Column(Integer, ForeignKey("whatsapp_grupos.id", ondelete="CASCADE"),
                      primary_key=True)
    posicao = Column(Integer, nullable=False, default=0)
    aberto = Column(Boolean, nullable=False, default=True)
    # "Cheio" e "Aberto" são dois eixos (espelho da 080). `aberto` é a decisão
    # da usuária; `cheio` é a ocupação — e antes desta coluna ele só existia
    # derivado, o que fazia um grupo com 946/900 aparecer "Aberto" para sempre.
    #
    # NULL = sem override (vale a regra automática); TRUE/FALSE = a usuária
    # sobrescreveu. Os dois casos reais que o override resolve: segurar um
    # grupo ANTES de lotar, e destravar quando o WhatsApp não atualizou a
    # contagem. Ver `cheio_efetivo` em campanha_grupos_service.
    cheio_override = Column(Boolean, nullable=True)
    adicionado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CampanhaNumero(Base):
    """Quais números a campanha usa (espelho da 079).

    Existe porque o roteador distribui o envio entre os números do conjunto, e
    porque a oferta de grupos precisa ser escopada: um grupo do número A numa
    campanha que dispara pelo B faz o envio falhar em silêncio — B não
    participa daquele grupo.
    """

    __tablename__ = "campanha_numeros"

    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         primary_key=True)
    instancia_id = Column(Integer, ForeignKey("whatsapp_instancias.id", ondelete="CASCADE"),
                          primary_key=True, index=True)
    adicionado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class CampanhaSubId(Base):
    """Sub IDs vinculados à CAMPANHA, à mão (espelho da 080).

    Diferente do vínculo de Anúncios, que é 1:1 por invariante de dinheiro:
    aqui a campanha aceita VÁRIOS. O total dos Resultados soma estes com os
    sub_ids dos grupos da campanha — e a dedup é obrigatória, porque o mesmo
    sub_id vinculado à mão E pertencente a um grupo contaria a comissão duas
    vezes.

    Sem UNIQUE global em `sub_id`: ele é texto livre, e um UNIQUE global faria
    uma afiliada impedir a outra de usar "promo1". A regra de "um sub_id só
    numa campanha" é validada no service, por usuária.
    """

    __tablename__ = "campanha_sub_ids"

    campanha_id = Column(Integer, ForeignKey("campanhas.id", ondelete="CASCADE"),
                         primary_key=True)
    # NORMALIZADO (o mesmo `normalizar_sub_id` do KpiService): sem isso "WGEA"
    # e "wgea" viram dois vínculos e a comissão entra em dobro.
    sub_id = Column(String(120), primary_key=True)
    vinculado_em = Column(DateTime(timezone=True), nullable=False,
                          server_default=func.now())
