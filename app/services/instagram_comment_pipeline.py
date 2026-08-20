"""Pipeline do comentário: do webhook até o direct.

Ordem (spec §6.2), e o porquê de cada passo:

1. Ignorar comentário da própria aluna — senão a resposta pública dela dispara a
   automação de novo, em loop.
2. Achar as automações ativas que cobrem aquele post.
3. Dedupe: por `comment_id` (UNIQUE no banco) e por pessoa+post.
4. Matching de palavra-chave.
5. Janela de 7 dias contada do TIMESTAMP DO COMENTÁRIO, não de quando o webhook
   chegou — fila travada queima janela.
6. Enviar: private reply primeiro, resposta pública depois.

A ordem do passo 6 é deliberada: se a DM falha, a resposta pública "te mandei no
direct" não sai — seria mentira visível para todo mundo no post.
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.instagram_automation import (
    DM_ENVIADO,
    DM_EXPIRADO,
    DM_FALHOU,
    DM_IGNORADO,
    DM_PROCESSANDO,
    DM_SEM_MATCH,
    TRIGGER_QUALQUER,
    InstagramAutomation,
    InstagramConnection,
    InstagramEvent,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.services import instagram_login_client as ig
from app.services.instagram_connection_service import InstagramConnectionService
from app.utils.text_normalize import comentario_casa

logger = logging.getLogger(__name__)

# A Meta aceita private reply só nos 7 dias seguintes à criação do comentário.
JANELA_PRIVATE_REPLY_DIAS = 7

REPLY_ENVIADO = "enviado"
REPLY_FALHOU = "falhou"
REPLY_PULADO = "pulado"
REPLY_NAO_APLICAVEL = "nao_aplicavel"


class ThrottleExcedido(Exception):
    """Teto horário de private replies atingido. Reenfileirar, não descartar."""

    def __init__(self, segundos_para_tentar: int):
        super().__init__(f"throttle: tentar de novo em {segundos_para_tentar}s")
        self.segundos_para_tentar = segundos_para_tentar


def parse_comment_timestamp(valor) -> Optional[datetime]:
    """Timestamp do comentário: ISO-8601 ou epoch em segundos, ambos aparecem."""
    if valor in (None, ""):
        return None
    if isinstance(valor, (int, float)):
        return datetime.fromtimestamp(float(valor), tz=timezone.utc)
    texto = str(valor).strip()
    if texto.isdigit():
        return datetime.fromtimestamp(int(texto), tz=timezone.utc)
    # A Graph API manda "+0000" (sem dois-pontos), que o fromisoformat do
    # Python 3.10 recusa.
    if len(texto) >= 5 and texto[-5] in "+-" and ":" not in texto[-5:]:
        texto = texto[:-2] + ":" + texto[-2:]
    if texto.endswith("Z"):
        texto = texto[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(texto)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def dentro_da_janela(comment_ts: Optional[datetime], agora: Optional[datetime] = None) -> bool:
    """Sem timestamp, assume dentro da janela.

    O webhook nem sempre traz `timestamp`. Descartar por falta do campo seria pior
    que tentar: uma tentativa fora da janela custa um erro da Meta; descartar
    custa uma aluna reclamando que a automação não funcionou.
    """
    if comment_ts is None:
        return True
    agora = agora or datetime.now(timezone.utc)
    return (agora - comment_ts) < timedelta(days=JANELA_PRIVATE_REPLY_DIAS)


def automacao_dispara(automacao: InstagramAutomation, texto: str) -> bool:
    """A automação reage a este comentário?"""
    if automacao.trigger_tipo == TRIGGER_QUALQUER:
        return True
    return comentario_casa(texto, automacao.palavras or [])


class InstagramCommentPipeline:
    def __init__(self, repo: InstagramAutomationRepository):
        self.repo = repo
        self.db: Session = repo.db
        self.conexao_service = InstagramConnectionService(repo)

    # ----------------------------- throttle ----------------------------- #

    def _checar_teto_horario(self, user_id: int) -> None:
        teto = settings.INSTAGRAM_MAX_PRIVATE_REPLIES_HORA
        enviados = self.repo.enviados_na_ultima_hora(user_id)
        if enviados >= teto:
            # Reenfileira com jitter: sem ele, os 150 comentários bloqueados
            # voltariam todos no mesmo segundo e estourariam o teto de novo.
            espera = 300 + random.randint(0, 120)
            logger.warning(
                "Instagram: teto horário atingido user_id=%s (%d/%d) — adiando %ds",
                user_id, enviados, teto, espera,
            )
            raise ThrottleExcedido(espera)

    async def _espacar_envio(self) -> None:
        """Respeita o limite auto-imposto de N envios por segundo.

        A API aceita mais, mas rajada é o que faz o Instagram tratar a conta como
        bot. Sem Redis, cai num sleep fixo — mais lento, nunca mais rápido que o
        limite.
        """
        por_segundo = max(1, settings.INSTAGRAM_MAX_ENVIOS_SEGUNDO)
        await asyncio.sleep(1.0 / por_segundo)

    # ---------------------------- processamento -------------------------- #

    async def processar_comentario(self, ig_user_id: str, valor: dict) -> dict:
        """Processa UM comentário do webhook. Nunca levanta erro de negócio.

        Devolve um dicionário com o desfecho, para o log da task.
        """
        comment_id = str((valor or {}).get("id") or "")
        if not comment_id:
            return {"status": "ignorado", "motivo": "sem comment_id"}

        conexao = self.repo.get_connection_by_ig_user_id(ig_user_id)
        if not conexao:
            return {"status": "ignorado", "motivo": "conta não conectada"}

        de = (valor or {}).get("from") or {}
        commenter_id = str(de.get("id") or "")
        commenter_username = de.get("username")
        texto = (valor or {}).get("text") or ""
        media = (valor or {}).get("media") or {}
        media_id = str(media.get("id") or "")
        comment_ts = parse_comment_timestamp((valor or {}).get("timestamp"))

        # 1) Comentário da própria aluna (inclusive a resposta pública que NÓS
        #    acabamos de postar) — sem isso, loop infinito.
        if commenter_id and commenter_id == str(conexao.ig_user_id):
            return {"status": "ignorado", "motivo": "comentário da própria conta"}

        # 2) Já processamos este comentário?
        if self.repo.get_event_by_comment(comment_id):
            return {"status": "duplicado", "motivo": "comment_id já processado"}

        # 3) Automações ativas que cobrem o post
        ativas = self.repo.active_automations_for_connection(conexao.id)
        candidatas = [a for a in ativas if a.cobre_media(media_id)]
        if not candidatas:
            return {"status": "ignorado", "motivo": "nenhuma automação cobre este post"}

        # 4) Matching. A primeira que casar é a que responde — a Meta só permite
        #    uma private reply por comentário, então não faz sentido tentar duas.
        automacao = next((a for a in candidatas if automacao_dispara(a, texto)), None)
        if automacao is None:
            self._registrar(
                conexao, None, comment_id, media_id, commenter_id, commenter_username,
                texto, comment_ts, DM_SEM_MATCH, reply_status=REPLY_NAO_APLICAVEL,
            )
            return {"status": "sem_match"}

        # 5) Dedupe por pessoa
        if self.repo.já_enviou_para_pessoa(automacao.id, media_id, commenter_id):
            self._registrar(
                conexao, automacao, comment_id, media_id, commenter_id, commenter_username,
                texto, comment_ts, "duplicado", reply_status=REPLY_NAO_APLICAVEL,
            )
            return {"status": "duplicado", "motivo": "pessoa já recebeu neste post"}

        # 6) Janela de 7 dias — sem gastar chamada
        if not dentro_da_janela(comment_ts):
            self._registrar(
                conexao, automacao, comment_id, media_id, commenter_id, commenter_username,
                texto, comment_ts, DM_EXPIRADO, reply_status=REPLY_NAO_APLICAVEL,
                erro_codigo="JANELA_7_DIAS",
                erro_mensagem="Comentário fora da janela de 7 dias da Meta.",
            )
            return {"status": "expirado"}

        # Teto horário antes de reservar — reservar e depois adiar deixaria o
        # comment_id ocupado e o comentário nunca seria reprocessado.
        self._checar_teto_horario(conexao.user_id)

        # 7) Reserva: a linha entra ANTES da chamada. O UNIQUE de comment_id
        #    trava um segundo worker que esteja processando o mesmo comentário.
        evento = self._registrar(
            conexao, automacao, comment_id, media_id, commenter_id, commenter_username,
            texto, comment_ts, DM_PROCESSANDO,
        )
        if evento is None:
            return {"status": "duplicado", "motivo": "corrida no comment_id"}

        return await self._enviar(conexao, automacao, evento, comment_id)

    async def _enviar(
        self,
        conexao: InstagramConnection,
        automacao: InstagramAutomation,
        evento: InstagramEvent,
        comment_id: str,
    ) -> dict:
        token = self.conexao_service.token_de(conexao)

        await self._espacar_envio()
        try:
            resposta = await ig.send_private_reply(
                token, conexao.ig_user_id, comment_id, automacao.dm_texto
            )
        except ig.InstagramApiError as exc:
            evento.dm_status = DM_FALHOU
            evento.reply_status = REPLY_PULADO
            evento.erro_codigo = exc.codigo_curto
            evento.erro_mensagem = exc.mensagem[:2000]
            self.db.commit()
            if exc.codigo == 190:
                # Token morreu no meio do caminho: pausa tudo e avisa, em vez de
                # falhar comentário por comentário até a aluna perceber.
                self.conexao_service.handle_token_invalido(conexao)
            if not exc.permanente:
                # Rede/5xx/rate limit: a task decide o retry.
                raise
            logger.info(
                "Instagram: DM não enviada (erro permanente) comment=%s: %s",
                comment_id, exc.mensagem,
            )
            return {"status": "falhou", "permanente": True, "erro": exc.mensagem}

        evento.dm_status = DM_ENVIADO
        evento.dm_message_id = str(resposta.get("message_id") or resposta.get("id") or "")[:128] or None
        self.db.commit()

        # Resposta pública só DEPOIS da DM ter dado certo.
        evento.reply_status = await self._responder_publicamente(token, automacao, comment_id)
        self.db.commit()
        return {"status": "enviado", "reply": evento.reply_status}

    async def _responder_publicamente(
        self, token: str, automacao: InstagramAutomation, comment_id: str
    ) -> str:
        if not automacao.resposta_publica_ativa:
            return REPLY_NAO_APLICAVEL
        variacoes = [v for v in (automacao.resposta_publica_variacoes or []) if v and v.strip()]
        if not variacoes:
            return REPLY_NAO_APLICAVEL

        indice = self.repo.bump_reply_index(automacao, len(variacoes))
        texto = variacoes[indice]

        await self._espacar_envio()
        try:
            await ig.reply_to_comment(token, comment_id, texto)
        except ig.InstagramApiError as exc:
            # Falha aqui não desfaz a DM, que já saiu. Registra e segue.
            logger.warning(
                "Instagram: resposta pública falhou comment=%s: %s", comment_id, exc.mensagem
            )
            return REPLY_FALHOU
        return REPLY_ENVIADO

    # ------------------------------ registro ----------------------------- #

    def _registrar(
        self,
        conexao: InstagramConnection,
        automacao: Optional[InstagramAutomation],
        comment_id: str,
        media_id: Optional[str],
        commenter_id: Optional[str],
        commenter_username: Optional[str],
        texto: Optional[str],
        comment_ts: Optional[datetime],
        dm_status: str,
        reply_status: Optional[str] = None,
        erro_codigo: Optional[str] = None,
        erro_mensagem: Optional[str] = None,
    ) -> Optional[InstagramEvent]:
        """Grava o evento. Devolve None se o comment_id já existia (corrida)."""
        evento = InstagramEvent(
            user_id=conexao.user_id,
            automation_id=automacao.id if automacao else None,
            comment_id=str(comment_id),
            media_id=str(media_id) if media_id else None,
            commenter_id=str(commenter_id) if commenter_id else None,
            commenter_username=commenter_username,
            comment_text=(texto or "")[:4000] or None,
            comment_timestamp=comment_ts,
            dm_status=dm_status,
            reply_status=reply_status,
            erro_codigo=erro_codigo,
            erro_mensagem=erro_mensagem,
        )
        try:
            self.repo.add_event(evento)
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            logger.info("Instagram: comment_id %s já registrado (corrida)", comment_id)
            return None
        return evento
