"""
Snapshot diário de grupos (F6) + reconciliação de sessões órfãs.

O snapshot é o que sustenta "evolução de membros" e o denominador do lucro por
pessoa. Roda 1×/dia (cron 064) — cadência de sync pesado nunca foi de hora em
hora aqui desde o incidente de 20/07.
"""
import logging
from datetime import date
from typing import Dict

from sqlalchemy.orm import Session

from app.models.whatsapp_grupos import INSTANCIA_CONECTADA
from app.repositories.campanha_link_repository import CampanhaLinkRepository
from app.repositories.whatsapp_instancia_repository import WhatsappInstanciaRepository
from app.services.janela_envio_service import BRT
from app.services.waha_client import ErroWhatsapp
from app.services.whatsapp_grupo_sync_service import WhatsappGrupoSyncService

logger = logging.getLogger(__name__)


def snapshot_do_usuario(db: Session, user_id: int) -> Dict[str, int]:
    """Sincroniza os grupos (que já atualiza participantes/nome/ativo) e grava
    o retrato do dia. Reaproveita o sync da F1 — uma fonte, não duas."""
    from datetime import datetime

    repo_inst = WhatsappInstanciaRepository(db)
    repo_link = CampanhaLinkRepository(db)
    hoje = datetime.now(BRT).date()

    resultado = {"instancias": 0, "grupos": 0, "erros": 0}
    conectadas = [i for i in repo_inst.por_usuario(user_id)
                  if i.status == INSTANCIA_CONECTADA]
    for instancia in conectadas:
        resultado["instancias"] += 1
        try:
            WhatsappGrupoSyncService(db).sincronizar(instancia, trigger="cron")
        except ErroWhatsapp as e:
            resultado["erros"] += 1
            logger.warning("Snapshot: sync da sessão %s falhou (%s)",
                           instancia.nome_instancia, e.motivo)

    from app.repositories.whatsapp_grupo_repository import WhatsappGrupoRepository

    for grupo in WhatsappGrupoRepository(db).por_usuario(user_id, apenas_ativos=True):
        repo_link.upsert_snapshot(grupo.id, hoje, grupo.participantes or 0,
                                  1 if grupo.sou_admin else 0)
        resultado["grupos"] += 1
    db.commit()
    return resultado


def reconciliar_orfas(db: Session) -> int:
    """Sessões que existem no WAHA e não no banco (deploy interrompido, delete
    que falhou) consomem RAM para sempre. Some com elas."""
    from app.core.config import settings
    from app.services.waha_client import WahaClient
    from app.services.whatsapp_instancia_service import (
        cliente_da_sessao, pertence_a_este_ambiente,
    )

    if not (settings.WAHA_URL and settings.WAHA_API_KEY):
        return 0
    cliente = WahaClient(settings.WAHA_URL, settings.WAHA_API_KEY, "-")
    try:
        status, dados = cliente._pedir("GET", "/api/sessions")
    except ErroWhatsapp as e:
        logger.warning("Reconciliação: não deu para listar sessões (%s)", e.motivo)
        return 0
    if status >= 400 or not isinstance(dados, list):
        return 0

    repo = WhatsappInstanciaRepository(db)
    removidas = 0
    for sessao in dados:
        nome = str((sessao or {}).get("name") or "")
        if not nome or nome == settings.WAHA_SESSAO_RESUMO:
            continue
        # Só mexe em sessão DESTE ambiente — hml e prod dividem o servidor.
        if not pertence_a_este_ambiente(nome):
            continue
        if repo.por_nome(nome) is None:
            try:
                cliente_da_sessao(nome).deletar_sessao()
                removidas += 1
                logger.warning("Sessão órfã removida do WAHA: %s", nome)
            except ErroWhatsapp as e:
                logger.warning("Falha ao remover órfã %s: %s", nome, e.motivo)
    return removidas
