"""Pool de proxies das sessões WAHA (anti-banimento) — espelho da migration 068.

Proxy aqui é **sticky**: cada chip fica com um IP fixo enquanto estiver
saudável. O que denuncia automação no WhatsApp é a TROCA de IP, não a
repetição — por isso `proxy_trocas` e `proxy_fixado_em` vivem na instância
(app/models/whatsapp_grupos.py) e a troca tem cooldown no serviço.

A credencial é guardada cifrada (Fernet, `app/core/encryption.py`) e só é
decifrada no instante de montar a chamada ao WAHA.
"""
from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.sql import func

from app.db.base import Base

# Tipos de proxy, do mais seguro ao mais queimado. Datacenter (AWS/OVH/Hetzner)
# é reconhecido e não deveria ser a escolha padrão para um chip de volume.
TIPO_MOVEL = "movel"
TIPO_RESIDENCIAL = "residencial"
TIPO_DATACENTER = "datacenter"
TIPOS = (TIPO_MOVEL, TIPO_RESIDENCIAL, TIPO_DATACENTER)

# Preferência de alocação: móvel > residencial > datacenter.
PRIORIDADE_TIPO = {TIPO_MOVEL: 0, TIPO_RESIDENCIAL: 1, TIPO_DATACENTER: 2}

PROXY_OK = "ok"
PROXY_DEGRADADO = "degradado"
PROXY_QUARENTENA = "quarentena"


class WhatsappProxy(Base):
    __tablename__ = "whatsapp_proxies"

    id = Column(Integer, primary_key=True, index=True)
    rotulo = Column(String(80), nullable=False)
    tipo = Column(String(16), nullable=False, default=TIPO_RESIDENCIAL)
    host = Column(String(255), nullable=False)
    porta = Column(Integer, nullable=False)
    # NUNCA em claro. Ver `proxy_pool_service.credenciais`.
    usuario_cifrado = Column(Text, nullable=True)
    senha_cifrada = Column(Text, nullable=True)
    pais = Column(String(2), nullable=False, default="BR")
    max_sessoes = Column(Integer, nullable=False, default=3)
    ativo = Column(Boolean, nullable=False, default=True)
    status = Column(String(16), nullable=False, default=PROXY_OK)
    falhas_seguidas = Column(Integer, nullable=False, default=0)
    ultimo_erro = Column(Text, nullable=True)
    ultimo_ip = Column(String(64), nullable=True)
    ultimo_pais = Column(String(8), nullable=True)
    verificado_em = Column(DateTime(timezone=True), nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    atualizado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    @property
    def servidor(self) -> str:
        """`host:porta` — o WAHA exige o server SEM esquema (`http://`)."""
        return f"{self.host}:{self.porta}"
