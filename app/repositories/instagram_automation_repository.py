import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    DM_ENVIADO,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
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

    def active_automations_for_connection(self, connection_id: int) -> List[InstagramAutomation]:
        return (
            self.db.query(InstagramAutomation)
            .filter(
                InstagramAutomation.connection_id == connection_id,
                InstagramAutomation.status == AUTOMACAO_ATIVA,
            )
            .all()
        )

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
