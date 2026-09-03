"""
Snapshot diário de grupos (F6) + reconciliação de sessões órfãs.

O snapshot é o que sustenta "evolução de membros" e o denominador do lucro por
pessoa. Roda 1×/dia (cron 064) — cadência de sync pesado nunca foi de hora em
hora aqui desde o incidente de 20/07.
"""
import logging
from datetime import date
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.whatsapp_grupos import INSTANCIA_CONECTADA, WhatsappInstancia
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

    # `apenas_ativados`: snapshot é monitoramento, e monitoramento é só do
    # que a usuária LIGOU (spec §6.2) — grupo existir no WhatsApp não basta.
    for grupo in WhatsappGrupoRepository(db).por_usuario(
            user_id, apenas_ativos=True, apenas_ativados=True):
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

    from app.services import waha_servidor_service

    # ⚠️ Multi-servidor (migration 071): varrer só o servidor padrão deixaria
    # órfã em shard não visitado viva PARA SEMPRE, consumindo a RAM que o pool
    # existe para administrar. E `cliente_da_sessao` não serve para apagar
    # órfã: ela não tem linha no banco (é essa a definição de órfã), então o
    # resolvedor cairia no padrão e o DELETE iria para a caixa errada — em
    # silêncio. Por isso o cliente é montado com o endereço de ONDE a sessão
    # foi encontrada.
    enderecos = _enderecos_para_varrer(db)
    if not enderecos:
        return 0

    repo = WhatsappInstanciaRepository(db)
    removidas = 0
    for rotulo, base_url, api_key in enderecos:
        try:
            status, dados = WahaClient(base_url, api_key, "-")._pedir("GET", "/api/sessions")
        except ErroWhatsapp as e:
            logger.warning("Reconciliação: não deu para listar sessões de %s (%s)",
                           rotulo, e.motivo)
            continue
        if status >= 400 or not isinstance(dados, list):
            continue

        for sessao in dados:
            nome = str((sessao or {}).get("name") or "")
            if not nome or nome == settings.WAHA_SESSAO_RESUMO:
                continue
            # Só mexe em sessão DESTE ambiente — hml e prod dividem o servidor.
            if not pertence_a_este_ambiente(nome):
                continue
            if repo.por_nome(nome) is not None:
                continue
            try:
                WahaClient(base_url, api_key, nome).deletar_sessao()
                removidas += 1
                logger.warning("Sessão órfã removida de %s: %s", rotulo, nome)
            except ErroWhatsapp as e:
                logger.warning("Falha ao remover órfã %s em %s: %s", nome, rotulo, e.motivo)
    return removidas


def _enderecos_para_varrer(db: Session) -> List[Tuple[str, str, Optional[str]]]:
    """(rótulo, base_url, api_key) de todo servidor que pode hospedar sessão
    nossa: os do pool + o padrão das envs, sem repetir base_url.

    O padrão entra mesmo com pool cadastrado porque sessão anterior à 071
    (servidor_id nulo) ainda vive lá.
    """
    from app.core.config import settings
    from app.services import waha_servidor_service
    from app.repositories.waha_servidor_repository import WahaServidorRepository

    enderecos: List[Tuple[str, str, Optional[str]]] = []
    vistos = set()

    try:
        for s in WahaServidorRepository(db).listar(ativos_apenas=True):
            if s.base_url and s.base_url not in vistos:
                vistos.add(s.base_url)
                enderecos.append((s.rotulo, s.base_url, waha_servidor_service.api_key(s)))
    except Exception:
        # Tabela ainda não existe (071 não aplicada) — o padrão abaixo cobre.
        logger.debug("Pool de servidores WAHA indisponível na reconciliação", exc_info=True)

    if settings.WAHA_URL and settings.WAHA_API_KEY and settings.WAHA_URL not in vistos:
        enderecos.append(("padrão (env)", settings.WAHA_URL, settings.WAHA_API_KEY))
    return enderecos


def reconciliar_eventos_de_sessao(db: Session) -> int:
    """
    Alinha os eventos assinados de cada sessão com o estado do monitoramento.

    O alinhamento normal acontece no toggle, mas ele pode falhar (sessão fora
    do ar, envio em andamento). O caso que importa é o assimétrico: uma sessão
    que continua assinando `message` depois de a afiliada desligar o
    monitoramento seguiria entregando conteúdo de grupo ao backend — que é
    exatamente o que a política de privacidade diz que não acontece.

    Devolve quantas sessões foram reconfiguradas.
    """
    from app.core.config import settings
    from app.models.whatsapp_grupos import INSTANCIA_REMOVIDA
    from app.services.monitoramento_service import MonitoramentoService
    from app.services.whatsapp_instancia_service import (
        EnvioEmAndamento, sincronizar_todas,
    )

    if not (settings.WAHA_URL and settings.WAHA_API_KEY):
        return 0
    repo = WhatsappInstanciaRepository(db)
    servico = MonitoramentoService(db)
    ajustadas = 0
    user_ids = [
        uid for (uid,) in
        db.query(WhatsappInstancia.user_id)
        .filter(WhatsappInstancia.status != INSTANCIA_REMOVIDA)
        .distinct().all()
    ]
    for user_id in user_ids:
        precisa = servico.sessoes_que_precisam_de_message(user_id)
        try:
            feitas, desconhecidas = sincronizar_todas(
                db, repo.por_usuario(user_id), precisa,
                settings.WAHA_WEBHOOK_URL or "",
            )
            ajustadas += feitas
            if desconhecidas:
                logger.warning("Sessões sem confirmação de eventos (user %s): %s",
                               user_id, ", ".join(desconhecidas))
        except EnvioEmAndamento:
            continue            # tenta de novo amanhã; o envio é prioritário
        except ErroWhatsapp as e:
            logger.warning("Reconciliação de eventos falhou (user %s): %s",
                           user_id, e.motivo)
    return ajustadas
