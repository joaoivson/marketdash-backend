"""
Entradas e saídas de grupo (F6) — o que sustenta evasão e "entraram e ficaram".

Vem do webhook `group.v2.participants` do WAHA. O diff de snapshot NÃO serve
aqui: ele dá só a contagem líquida, e sem saber QUEM entrou e QUEM saiu não dá
para dizer quantos dos que entraram continuam no grupo — que é o número que
decide se vale pagar por mais uma pessoa.

LGPD: o JID do participante é convertido em `sha256(jid + salt)` NESTE handler,
antes de qualquer persistência. O número cru nunca chega ao banco nem ao log.
"""
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.campanha_link import (
    EVENTO_ENTRADA, EVENTO_SAIDA, ORIGEM_DESCONHECIDA, ORIGEM_LINK,
    ORIGEM_ORGANICA,
)
from app.repositories.campanha_link_repository import CampanhaLinkRepository
from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository

logger = logging.getLogger(__name__)

# Janela da heurística: entrada logo depois de um clique roteado ao mesmo
# grupo é atribuída ao link. Fora dela, é orgânica.
JANELA_ATRIBUICAO_MIN = 15

ACOES = {"join": EVENTO_ENTRADA, "leave": EVENTO_SAIDA}


def identificador(jid: str) -> str:
    """sha256(jid + salt). Sem salt configurado ainda hasheia — o objetivo é
    não guardar o número, e um hash sem salt já cumpre isso (embora seja
    reversível por força bruta; o salt é o que fecha a porta)."""
    salt = settings.WHATSAPP_HASH_SALT or ""
    return hashlib.sha256(f"{jid}|{salt}".encode()).hexdigest()


class GrupoEventoService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = CampanhaLinkRepository(db)
        self.repo_grupos = WhatsappGrupoRepository(db)

    def registrar(self, user_id: int, jid_grupo: str, acao: str,
                  jids_participantes: List[str]) -> int:
        """Processa um evento de participantes. Devolve quantos foram gravados."""
        tipo = ACOES.get((acao or "").lower())
        if not tipo or not jids_participantes:
            return 0   # promote/demote não são entrada nem saída

        grupo = self.repo_grupos.por_jid(user_id, jid_grupo)
        if not grupo:
            logger.info("Evento de grupo desconhecido (%s) — ignorado", jid_grupo[:20])
            return 0

        desde = datetime.now(timezone.utc) - timedelta(minutes=JANELA_ATRIBUICAO_MIN)
        clique = (
            self.repo.clique_recente_do_grupo(grupo.id, desde)
            if tipo == EVENTO_ENTRADA else None
        )

        gravados = 0
        for jid in jids_participantes:
            if not jid:
                continue
            if tipo == EVENTO_ENTRADA:
                origem = ORIGEM_LINK if clique else ORIGEM_ORGANICA
                link_evento_id = clique.id if clique else None
            else:
                origem = ORIGEM_DESCONHECIDA
                link_evento_id = None
            self.repo.registrar_evento_de_grupo(
                grupo.id, tipo, origem, identificador(jid), link_evento_id
            )
            gravados += 1

        # O contador do grupo acompanha o evento: a tela não espera o snapshot
        # da madrugada para mostrar que o grupo encheu.
        delta = gravados if tipo == EVENTO_ENTRADA else -gravados
        grupo.participantes = max(0, (grupo.participantes or 0) + delta)
        self.db.add(grupo)
        self.db.commit()
        logger.info("Grupo %s: %s %s participante(s)", grupo.id, tipo, gravados)
        return gravados
