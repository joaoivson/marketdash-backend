import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    DM_ENVIADO,
    ESCOPO_POST_ESPECIFICO,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
    InstagramMidiaDetectada,
)

logger = logging.getLogger(__name__)


class InstagramAutomationRepository:
    def __init__(self, db: Session):
        self.db = db

    # ---------------------------- conexões ---------------------------- #

    def get_connection_by_user(self, user_id: int) -> Optional[InstagramConnection]:
        return (
            self.db.query(InstagramConnection)
            .filter(InstagramConnection.user_id == user_id)
            .first()
        )

    def get_connection_by_ig_user_id(self, ig_user_id: str) -> Optional[InstagramConnection]:
        """Caminho quente do webhook: a Meta identifica a conta pelo ig_user_id."""
        return (
            self.db.query(InstagramConnection)
            .filter(InstagramConnection.ig_user_id == str(ig_user_id))
            .first()
        )

    def upsert_connection(
        self,
        user_id: int,
        ig_user_id: str,
        ig_username: Optional[str],
        ig_avatar_url: Optional[str],
        account_type: Optional[str],
        access_token_encrypted: str,
        token_expires_at: Optional[datetime],
        scopes: Optional[str],
    ) -> InstagramConnection:
        conexao = self.get_connection_by_user(user_id)
        if conexao is None:
            conexao = InstagramConnection(user_id=user_id)
            self.db.add(conexao)
        conexao.ig_user_id = str(ig_user_id)
        conexao.ig_username = ig_username
        conexao.ig_avatar_url = ig_avatar_url
        conexao.account_type = account_type
        conexao.access_token = access_token_encrypted
        conexao.token_expires_at = token_expires_at
        conexao.scopes = scopes
        conexao.status = CONEXAO_ATIVA
        conexao.connected_at = conexao.connected_at or datetime.now(timezone.utc)
        self.db.flush()
        return conexao

    def set_connection_status(self, conexao: InstagramConnection, status: str) -> None:
        conexao.status = status
        self.db.flush()

    def delete_connection(self, user_id: int) -> bool:
        """Remove a conexão. Automações e eventos caem por ON DELETE CASCADE."""
        removidas = (
            self.db.query(InstagramConnection)
            .filter(InstagramConnection.user_id == user_id)
            .delete()
        )
        self.db.flush()
        return removidas > 0

    def connections_needing_refresh(self, dias: int = 10) -> List[InstagramConnection]:
        """Conexões ativas cujo token vence em menos de `dias`.

        Inclui `token_expires_at IS NULL` — conexão antiga sem validade registrada
        não pode ficar de fora da renovação, senão vence em silêncio.
        """
        limite = datetime.now(timezone.utc) + timedelta(days=dias)
        return (
            self.db.query(InstagramConnection)
            .filter(
                InstagramConnection.status == CONEXAO_ATIVA,
                (InstagramConnection.token_expires_at.is_(None))
                | (InstagramConnection.token_expires_at <= limite),
            )
            .all()
        )

    # --------------------------- automações --------------------------- #

    def list_automations(self, user_id: int) -> List[InstagramAutomation]:
        return (
            self.db.query(InstagramAutomation)
            .filter(InstagramAutomation.user_id == user_id)
            .order_by(InstagramAutomation.created_at.desc())
            .all()
        )

    def get_automation(self, user_id: int, automation_id: int) -> Optional[InstagramAutomation]:
        return (
            self.db.query(InstagramAutomation)
            .filter(
                InstagramAutomation.user_id == user_id,
                InstagramAutomation.id == automation_id,
            )
            .first()
        )

    def get_automation_de_qualquer_conta(self, automation_id: int) -> Optional[InstagramAutomation]:
        """SÓ para rota de admin (suporte). Toda rota de aluna usa get_automation."""
        return (
            self.db.query(InstagramAutomation)
            .filter(InstagramAutomation.id == automation_id)
            .first()
        )

    def active_automations_for_connection(self, connection_id: int) -> List[InstagramAutomation]:
        """Automações ativas da conta, em ordem ESTÁVEL.

        O `order_by(id)` não é enfeite: o pipeline responde com a PRIMEIRA que
        casa, e sem ordenação explícita o Postgres devolve na ordem que quiser.
        Duas automações que casam com o mesmo comentário responderiam ora uma,
        ora outra — bug que só aparece em produção e não reproduz.

        A ordem por ESPECIFICIDADE (post escolhido ganha de "qualquer post") é
        regra de negócio e mora no pipeline, não aqui.
        """
        return (
            self.db.query(InstagramAutomation)
            .filter(
                InstagramAutomation.connection_id == connection_id,
                InstagramAutomation.status == AUTOMACAO_ATIVA,
            )
            .order_by(InstagramAutomation.id)
            .all()
        )

    def media_ids_com_automacao_ativa(self, connection_id: int) -> frozenset[str]:
        """Posts que já têm automação ATIVA apontando para eles.

        Só escopo de post: `qualquer` não aponta para mídia nenhuma, e contá-lo
        aqui marcaria as 283 publicações como cobertas — escondendo exatamente
        o buraco que este campo existe para mostrar.
        """
        linhas = (
            self.db.query(InstagramAutomation.media_id)
            .filter(
                InstagramAutomation.connection_id == connection_id,
                InstagramAutomation.status == AUTOMACAO_ATIVA,
                InstagramAutomation.escopo == ESCOPO_POST_ESPECIFICO,
                InstagramAutomation.media_id.isnot(None),
            )
            .all()
        )
        return frozenset(str(l[0]) for l in linhas if l[0])

    def add_automation(self, automation: InstagramAutomation) -> InstagramAutomation:
        self.db.add(automation)
        self.db.flush()
        return automation

    def delete_automation(self, automation: InstagramAutomation) -> None:
        self.db.delete(automation)
        self.db.flush()

    def pause_all_for_connection(self, connection_id: int) -> int:
        """Pausa as automações da conexão. Usado no deauthorize e no token vencido.

        Pausar (e não apagar) é intencional: quando a aluna reconectar, ela
        reencontra tudo como deixou e só precisa religar.
        """
        atualizadas = (
            self.db.query(InstagramAutomation)
            .filter(
                InstagramAutomation.connection_id == connection_id,
                InstagramAutomation.status == AUTOMACAO_ATIVA,
            )
            .update({"status": "pausada"}, synchronize_session=False)
        )
        self.db.flush()
        return atualizadas

    def bump_reply_index(self, automation: InstagramAutomation, total_variacoes: int) -> int:
        """Avança a rotação de resposta pública e devolve o índice a usar AGORA.

        Persistido no banco de propósito: um contador em memória zeraria a cada
        reinício de worker e a primeira variação apareceria muito mais que as outras.
        """
        if total_variacoes <= 0:
            return 0
        atual = (automation.resposta_publica_indice or 0) % total_variacoes
        automation.resposta_publica_indice = (atual + 1) % total_variacoes
        self.db.flush()
        return atual

    # ----------------------------- eventos ----------------------------- #

    def get_event_by_comment(self, comment_id: str) -> Optional[InstagramEvent]:
        return (
            self.db.query(InstagramEvent)
            .filter(InstagramEvent.comment_id == str(comment_id))
            .first()
        )

    def já_enviou_para_pessoa(
        self, automation_id: int, media_id: Optional[str], commenter_id: Optional[str]
    ) -> bool:
        """Dedupe por pessoa: mesma automação + mesmo post + mesma pessoa.

        Só considera envio que DEU CERTO. Se a tentativa anterior falhou, a pessoa
        não recebeu nada e um novo comentário dela merece nova tentativa.
        """
        if not commenter_id:
            return False
        q = self.db.query(InstagramEvent.id).filter(
            InstagramEvent.automation_id == automation_id,
            InstagramEvent.commenter_id == str(commenter_id),
            InstagramEvent.dm_status == DM_ENVIADO,
        )
        if media_id:
            q = q.filter(InstagramEvent.media_id == str(media_id))
        return self.db.query(q.exists()).scalar() is True

    def status_dos_comentarios(self, user_id: int, comment_ids: List[str]) -> Dict[str, str]:
        """`comment_id → dm_status` dos que já passaram pelo pipeline, em lote."""
        if not comment_ids:
            return {}
        linhas = (
            self.db.query(InstagramEvent.comment_id, InstagramEvent.dm_status)
            .filter(
                InstagramEvent.user_id == user_id,
                InstagramEvent.comment_id.in_([str(c) for c in comment_ids]),
            )
            .all()
        )
        return {str(c): s for c, s in linhas}

    def pessoas_que_receberam(self, user_id: int, automation_id: int) -> set:
        """Quem já recebeu o direct DESTA automação — a mesma régua do dedupe por pessoa."""
        linhas = (
            self.db.query(InstagramEvent.commenter_id)
            .filter(
                InstagramEvent.user_id == user_id,
                InstagramEvent.automation_id == automation_id,
                InstagramEvent.dm_status == DM_ENVIADO,
                InstagramEvent.commenter_id.isnot(None),
            )
            .distinct()
            .all()
        )
        return {str(c) for (c,) in linhas}

    # ------------------------- mídias detectadas ------------------------- #

    def registrar_midia_comentada(
        self,
        conexao: InstagramConnection,
        media_id: str,
        ad_id: Optional[str] = None,
        ad_title: Optional[str] = None,
        original_media_id: Optional[str] = None,
        quando: Optional[datetime] = None,
        media_product_type: Optional[str] = None,
    ) -> InstagramMidiaDetectada:
        """Upsert da mídia comentada (migration 085). Não commita — quem chama decide.

        `ad_id` no webhook é certeza de anúncio; sem ele, `eh_anuncio` fica como
        estava (None até a listagem conferir contra as orgânicas).
        """
        quando = quando or datetime.now(timezone.utc)
        midia = (
            self.db.query(InstagramMidiaDetectada)
            .filter(
                InstagramMidiaDetectada.connection_id == conexao.id,
                InstagramMidiaDetectada.media_id == str(media_id),
            )
            .first()
        )
        if midia is None:
            midia = InstagramMidiaDetectada(
                user_id=conexao.user_id,
                connection_id=conexao.id,
                media_id=str(media_id),
                comentarios=0,
                primeiro_comentario_em=quando,
            )
            self.db.add(midia)
        midia.comentarios = (midia.comentarios or 0) + 1
        midia.ultimo_comentario_em = quando
        if ad_id:
            midia.ad_id = str(ad_id)
            midia.eh_anuncio = True
        if media_product_type:
            midia.media_product_type = str(media_product_type)
            if media_product_type == "AD":
                midia.eh_anuncio = True
        if ad_title:
            midia.ad_title = ad_title
        if original_media_id:
            midia.original_media_id = str(original_media_id)
        self.db.flush()
        return midia

    def midias_detectadas(self, connection_id: int) -> List[InstagramMidiaDetectada]:
        """As que não foram descartadas como post do feed, mais comentada recente primeiro."""
        return (
            self.db.query(InstagramMidiaDetectada)
            .filter(
                InstagramMidiaDetectada.connection_id == connection_id,
                InstagramMidiaDetectada.eh_anuncio.isnot(False),
            )
            .order_by(
                InstagramMidiaDetectada.ultimo_comentario_em.desc(),
                InstagramMidiaDetectada.id.desc(),
            )
            .all()
        )

    def automacao_vinculada_id(self, connection_id: int, media_id: str) -> Optional[int]:
        """Caminho quente do webhook: este anúncio responde por qual automação?"""
        if not media_id:
            return None
        linha = (
            self.db.query(InstagramMidiaDetectada.automation_id)
            .filter(
                InstagramMidiaDetectada.connection_id == connection_id,
                InstagramMidiaDetectada.media_id == str(media_id),
                InstagramMidiaDetectada.automation_id.isnot(None),
            )
            .first()
        )
        return int(linha[0]) if linha else None

    def midias_vinculadas_ids(self, automation_id: int) -> List[str]:
        linhas = (
            self.db.query(InstagramMidiaDetectada.media_id)
            .filter(InstagramMidiaDetectada.automation_id == automation_id)
            .order_by(InstagramMidiaDetectada.id)
            .all()
        )
        return [str(m) for (m,) in linhas]

    def vinculos_por_automacao(self, user_id: int) -> Dict[int, List[str]]:
        """{automation_id: [media_id de anúncio]} em uma query, para a lista de cards."""
        linhas = (
            self.db.query(InstagramMidiaDetectada.automation_id, InstagramMidiaDetectada.media_id)
            .filter(
                InstagramMidiaDetectada.user_id == user_id,
                InstagramMidiaDetectada.automation_id.isnot(None),
            )
            .order_by(InstagramMidiaDetectada.id)
            .all()
        )
        saida: Dict[int, List[str]] = {}
        for aid, media_id in linhas:
            saida.setdefault(int(aid), []).append(str(media_id))
        return saida

    def anuncios_da_conexao(
        self, connection_id: int, media_ids: List[str]
    ) -> List[InstagramMidiaDetectada]:
        if not media_ids:
            return []
        return (
            self.db.query(InstagramMidiaDetectada)
            .filter(
                InstagramMidiaDetectada.connection_id == connection_id,
                InstagramMidiaDetectada.media_id.in_([str(m) for m in media_ids]),
                InstagramMidiaDetectada.eh_anuncio.is_(True),
            )
            .all()
        )

    def desvincular_anuncios(self, automation_id: int) -> None:
        self.db.query(InstagramMidiaDetectada).filter(
            InstagramMidiaDetectada.automation_id == automation_id
        ).update({InstagramMidiaDetectada.automation_id: None}, synchronize_session="fetch")

    def add_event(self, evento: InstagramEvent) -> InstagramEvent:
        self.db.add(evento)
        self.db.flush()
        return evento

    def enviados_na_ultima_hora(self, user_id: int) -> int:
        """Quantos directs saíram nos últimos 60 min — base do throttle horário."""
        desde = datetime.now(timezone.utc) - timedelta(hours=1)
        return (
            self.db.query(func.count(InstagramEvent.id))
            .filter(
                InstagramEvent.user_id == user_id,
                InstagramEvent.dm_status == DM_ENVIADO,
                InstagramEvent.processed_at >= desde,
            )
            .scalar()
            or 0
        )

    def contadores_por_automacao(self, user_id: int) -> Dict[int, dict]:
        """{automation_id: {comentarios, directs}} para os cards da lista."""
        linhas = (
            self.db.query(
                InstagramEvent.automation_id.label("aid"),
                func.count(InstagramEvent.id).label("comentarios"),
                func.count(InstagramEvent.id)
                .filter(InstagramEvent.dm_status == DM_ENVIADO)
                .label("directs"),
            )
            .filter(
                InstagramEvent.user_id == user_id,
                InstagramEvent.automation_id.isnot(None),
            )
            .group_by(InstagramEvent.automation_id)
            .all()
        )
        return {
            int(l.aid): {
                "comentarios": int(l.comentarios or 0),
                "directs": int(l.directs or 0),
            }
            for l in linhas
        }

    def list_events(self, user_id: int, limit: int = 50) -> List[InstagramEvent]:
        return (
            self.db.query(InstagramEvent)
            .filter(InstagramEvent.user_id == user_id)
            .order_by(InstagramEvent.processed_at.desc())
            .limit(limit)
            .all()
        )
