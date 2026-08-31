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
from app.services.waha_client import ErroWhatsapp

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

        if tipo == EVENTO_ENTRADA:
            self._expulsar_bloqueados(user_id, grupo, jids_participantes)
        return gravados

    def _expulsar_bloqueados(self, user_id: int, grupo,
                             jids_participantes: List[str]) -> None:
        """
        Número na blacklist que entra num grupo dela sai na hora.

        Roda DEPOIS do commit do evento de propósito: a entrada aconteceu e tem
        que constar no histórico mesmo que a remoção falhe — apagar o rastro
        deixaria a evasão e o "entraram e ficaram" mentindo.

        Só age quando ela é admin do grupo. Sem isso, o WAHA devolve 403 a cada
        entrada e enche o log com um erro que não é erro.
        """
        from app.services.blacklist_service import BlacklistService, numero_de_jid

        if not getattr(grupo, "sou_admin", False):
            return
        servico = BlacklistService(self.db)
        # Uma query para o lote todo: uma entrada em massa (link divulgado)
        # traz dezenas de JIDs de uma vez, e consultar um a um seria N+1 no
        # caminho do webhook.
        por_numero = {numero_de_jid(j or ""): j for j in jids_participantes}
        por_numero.pop(None, None)
        bloqueados = servico.bloqueados_entre(user_id, por_numero.keys())
        if not bloqueados:
            return
        for numero in bloqueados:
            jid = por_numero[numero]
            item = servico.bloqueado(user_id, numero)
            if not item or not item.remover_dos_grupos:
                continue
            try:
                self._cliente_do_grupo(user_id, grupo).remover_participante(
                    grupo.jid, jid
                )
                logger.info("Blacklist: participante removido do grupo %s", grupo.id)
            except ErroWhatsapp as e:
                # Nunca propaga: isto roda dentro do handler do webhook, e uma
                # exceção aqui faria o WAHA reenviar o evento em laço.
                logger.warning("Blacklist: falha ao remover do grupo %s (%s)",
                               grupo.id, e.motivo)
            except Exception:
                logger.exception("Blacklist: falha inesperada ao remover do grupo %s",
                                 grupo.id)

    def _cliente_do_grupo(self, user_id: int, grupo):
        """Sessão conectada que é membro deste grupo."""
        from app.models.whatsapp_grupos import (
            INSTANCIA_CONECTADA, WhatsappGrupoInstancia, WhatsappInstancia,
        )
        from app.services.whatsapp_instancia_service import cliente_da_sessao

        instancia = (
            self.db.query(WhatsappInstancia)
            .join(WhatsappGrupoInstancia,
                  WhatsappGrupoInstancia.instancia_id == WhatsappInstancia.id)
            # Remover alguém do grupo é escrita ATIVA no WhatsApp pelo chip:
            # um chip pausado não age, mesmo conectado.
            .filter(WhatsappGrupoInstancia.grupo_id == grupo.id,
                    WhatsappInstancia.user_id == user_id,
                    WhatsappInstancia.status == INSTANCIA_CONECTADA,
                    WhatsappInstancia.envio_pausado.is_(False))
            .first()
        )
        if not instancia:
            raise ErroWhatsapp("sem_instancia", "nenhuma sessão conectada no grupo")
        return cliente_da_sessao(instancia.nome_instancia)
