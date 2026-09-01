"""Pool de servidores WAHA — espelho da migration 071.

Antes disso o servidor era UM, fixo em `settings.WAHA_URL`, e não havia para
onde apontar a sessão 61. Com o pool, aumentar capacidade é INSERT: nem deploy,
nem migration, nem tocar no motor de envio.

⚠️ **A alocação é definitiva.** O estado do whatsmeow vive no Postgres do WAHA
que pareou a sessão, então trocar `servidor_id` depois NÃO move a sessão — só
faz o backend falar com a caixa errada. Para esvaziar um servidor: `aceita_novas
= False` e espera a rotatividade, ou re-pareia com aviso à afiliada.

A API key fica cifrada (Fernet, `app/core/encryption.py`) e só é decifrada no
instante de montar a chamada — ela é a chave de TODOS os números daquele
servidor, não de um.
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base import Base

# Mesma escala do pool de proxy: `ok` aloca, `degradado` continua servindo quem
# já está lá mas não recebe sessão nova, `fora` não serve para nada.
SERVIDOR_OK = "ok"
SERVIDOR_DEGRADADO = "degradado"
SERVIDOR_FORA = "fora"


class WahaServidor(Base):
    __tablename__ = "waha_servidores"

    id = Column(Integer, primary_key=True, index=True)
    rotulo = Column(String(60), nullable=False, unique=True)
    # Rede interna ou VPN — nunca porta pública.
    base_url = Column(String(255), nullable=False)
    # NUNCA em claro. Ver `waha_servidor_service.api_key`.
    api_key_cifrada = Column(Text, nullable=False)
    # Teto DESTE servidor, editável em runtime. A RAM por sessão do WAHA nunca
    # foi medida; deixar o número no banco permite subir conforme as sessões
    # reais entram, em vez de exigir o chute certo antes de comprar a caixa.
    max_sessoes = Column(Integer, nullable=False, default=60)
    ativo = Column(Boolean, nullable=False, default=True)
    # Drenar sem desligar: False para de alocar, as sessões atuais continuam.
    aceita_novas = Column(Boolean, nullable=False, default=True)
    status = Column(String(16), nullable=False, default=SERVIDOR_OK)
    falhas_seguidas = Column(Integer, nullable=False, default=0)
    ultimo_erro = Column(Text, nullable=True)
    verificado_em = Column(DateTime(timezone=True), nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def disponivel(self) -> bool:
        """Serve para receber sessão NOVA? (ter vaga é conta do serviço.)"""
        return bool(self.ativo) and bool(self.aceita_novas) and self.status == SERVIDOR_OK
