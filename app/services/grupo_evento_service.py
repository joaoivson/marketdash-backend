"""
Entradas e saídas de grupo (F6) — o que sustenta evasão e "entraram e ficaram".

Vem do webhook `group.v2.participants` do WAHA. O diff de snapshot NÃO serve
aqui: ele dá só a contagem líquida, e sem saber QUEM entrou e QUEM saiu não dá
para dizer quantos dos que entraram continuam no grupo — que é o número que
decide se vale pagar por mais uma pessoa.

LGPD: o JID do participante vira um pseudônimo NESTE handler, antes de qualquer
persistência — o número cru nunca chega ao banco nem ao log. O pseudônimo é
`HMAC-SHA256(segredo, jid)`, e o segredo nunca é vazio: `sha256` de telefone sem
segredo é reversível em minutos, e a política de privacidade promete o
contrário. Ver `_segredo_do_hash`.
"""
import hashlib
import hmac
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


def _segredo_do_hash() -> str:
    """
    Segredo do HMAC. **Nunca vazio** — e essa é a regra inteira.

    A versão anterior fazia `sha256(jid + (salt or ""))` e seguia em frente sem
    salt configurado, justificando que "já não guarda o número". Não cumpre:
    telefone tem espaço de busca minúsculo. Medido nesta máquina, em Python
    puro, 1,5 milhão de hashes/s — **o espaço inteiro de celulares brasileiros
    (~1,08 bilhão) cai em ~11 minutos**, e numa GPU em segundos. Ou seja: o
    "código irreversível" que a política de privacidade promete era reversível.

    Como `WHATSAPP_HASH_SALT` é opcional e não estava setada em ambiente nenhum,
    depender dela era garantir o pior caso. Sem ela, derivamos de um segredo que
    o app já exige para funcionar — assim o pseudônimo nasce protegido em
    qualquer instalação, sem depender de alguém lembrar de uma env var.
    """
    explicito = (settings.WHATSAPP_HASH_SALT or "").strip()
    if explicito:
        return explicito
    base = (settings.SHOPEE_ENCRYPTION_KEY or "") or (getattr(settings, "JWT_SECRET", "") or "")
    if not base:
        raise RuntimeError(
            "Sem WHATSAPP_HASH_SALT e sem segredo do qual derivá-la: não dá para "
            "transformar o número de terceiro em algo irreversível, então o "
            "evento não pode ser registrado."
        )
    return hashlib.sha256(f"marketdash|grupo-evento|{base}".encode()).hexdigest()


def identificador(jid: str) -> str:
    """
    Pseudônimo estável do participante: HMAC-SHA256(segredo, jid).

    Keyed hash, não `sha256(dado + sal)`: com o segredo fora do banco, vazar o
    banco sozinho não devolve nenhum número. O valor é estável para o mesmo JID,
    que é o que permite casar entrada com saída ("entraram e ficaram") sem
    jamais guardar o telefone.
    """
    return hmac.new(_segredo_do_hash().encode(), (jid or "").encode(),
                    hashlib.sha256).hexdigest()


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
