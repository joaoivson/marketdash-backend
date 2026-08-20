"""Conexão da aluna com o Instagram: OAuth, tokens, renovação e desconexão.

Business Login for Instagram — ver `instagram_login_client` para o porquê de não
usar Facebook Login aqui.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.encryption import decrypt_value, encrypt_value
from app.models.instagram_automation import (
    CONEXAO_ATIVA,
    CONEXAO_EXPIRADA,
    CONEXAO_REVOGADA,
    InstagramConnection,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import InstagramConnectionResponse
from app.services import instagram_login_client as ig

logger = logging.getLogger(__name__)

# O token longo vale 60 dias. Renovamos com 10 dias de folga: se o backend ficar
# fora do ar alguns dias, ainda sobra janela — e token vencido NÃO renova, exige
# login manual da aluna.
DIAS_FOLGA_RENOVACAO = 10


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _com_fuso(dt: Optional[datetime]) -> Optional[datetime]:
    """Postgres devolve timestamptz com fuso, SQLite devolve naive. Normaliza."""
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class InstagramConnectionService:
    def __init__(self, repo: InstagramAutomationRepository):
        self.repo = repo
        self.db: Session = repo.db

    # ------------------------------ estado ------------------------------ #

    def _resolver_status(self, conexao: InstagramConnection) -> str:
        """Reavalia o status considerando o vencimento do token."""
        if conexao.status == CONEXAO_REVOGADA:
            return CONEXAO_REVOGADA
        expira = _com_fuso(conexao.token_expires_at)
        if expira and _agora() > expira:
            if conexao.status != CONEXAO_EXPIRADA:
                conexao.status = CONEXAO_EXPIRADA
                self.repo.pause_all_for_connection(conexao.id)
                self.db.commit()
                logger.warning(
                    "Instagram: token vencido user_id=%s — automações pausadas", conexao.user_id
                )
            return CONEXAO_EXPIRADA
        return conexao.status or CONEXAO_ATIVA

    @staticmethod
    def _montar_resposta(conexao: InstagramConnection) -> InstagramConnectionResponse:
        """Resposta da conexão, com os alertas derivados dos escopos concedidos."""
        resp = InstagramConnectionResponse.model_validate(conexao)
        concedidos = [e.strip() for e in (conexao.scopes or "").split(",") if e.strip()]
        # Sem lista de escopos não dá pra afirmar que falta algo — não alarmar.
        resp.pode_responder_comentario = (
            ig.ESCOPO_COMENTARIOS in concedidos if concedidos else True
        )
        return resp

    def get_status(self, user_id: int) -> Optional[InstagramConnectionResponse]:
        conexao = self.repo.get_connection_by_user(user_id)
        if not conexao:
            return None
        status_atual = self._resolver_status(conexao)
        resp = self._montar_resposta(conexao)
        resp.status = status_atual
        return resp

    def require_conexao_ativa(self, user_id: int) -> InstagramConnection:
        conexao = self.repo.get_connection_by_user(user_id)
        if not conexao or self._resolver_status(conexao) != CONEXAO_ATIVA:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "INSTAGRAM_NAO_CONECTADO",
                    "message": "Conecte sua conta do Instagram antes de continuar.",
                },
            )
        return conexao

    def token_de(self, conexao: InstagramConnection) -> str:
        return decrypt_value(conexao.access_token)

    # ------------------------------- OAuth ------------------------------- #

    def _redirect_uri(self, informado: Optional[str]) -> str:
        uri = informado or settings.INSTAGRAM_OAUTH_REDIRECT_URI
        if not uri:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="INSTAGRAM_OAUTH_REDIRECT_URI não configurado no servidor.",
            )
        return uri

    def build_authorize_url(self, redirect_uri: Optional[str]) -> str:
        return ig.build_authorize_url(self._redirect_uri(redirect_uri), secrets.token_urlsafe(16))

    async def handle_oauth_callback(
        self, user_id: int, code: str, redirect_uri: Optional[str]
    ) -> InstagramConnectionResponse:
        uri = self._redirect_uri(redirect_uri)

        try:
            curto = await ig.exchange_code_for_short_token(code, uri)
            short_token = curto.get("access_token")
            if not short_token:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="O Instagram não devolveu um token de acesso.",
                )

            longo = await ig.exchange_for_long_lived_token(short_token)
            access_token = longo.get("access_token") or short_token
            expira_em = int(longo.get("expires_in") or 0)
            token_expires_at = _agora() + timedelta(seconds=expira_em) if expira_em else None

            perfil = await ig.get_me(access_token)
        except ig.InstagramApiError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.mensagem)

        tipo_conta = (perfil.get("account_type") or "").upper()
        if tipo_conta and tipo_conta not in ig.TIPOS_PROFISSIONAIS:
            # Conta pessoal não expõe comentários nem mensagens. Erro explícito,
            # com o caminho da solução — é a causa nº 1 de "não funcionou".
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={
                    "code": "CONTA_NAO_PROFISSIONAL",
                    "message": (
                        "Sua conta precisa ser Profissional (Comercial ou Criador de "
                        "Conteúdo). Converta no app do Instagram e conecte novamente."
                    ),
                },
            )

        ig_user_id = str(perfil.get("user_id") or perfil.get("id") or "")
        if not ig_user_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não foi possível identificar a conta do Instagram.",
            )

        # Uma conta do Instagram só pode estar ligada a um usuário do MarketDash:
        # o webhook chega identificado pelo ig_user_id e não teria como escolher
        # entre dois donos.
        de_outro = self.repo.get_connection_by_ig_user_id(ig_user_id)
        if de_outro and de_outro.user_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "INSTAGRAM_JA_CONECTADO",
                    "message": (
                        "Esta conta do Instagram já está conectada a outra conta do "
                        "MarketDash. Desconecte lá antes de conectar aqui."
                    ),
                },
            )

        # Guardamos o que foi CONCEDIDO, não o que pedimos. Se a aluna desmarcar
        # uma permissão na tela de consentimento, tudo conecta e só o recurso
        # daquela permissão para de funcionar — em silêncio. Com a lista real, dá
        # pra avisar. Se a Meta não devolver `permissions`, caímos no que pedimos
        # e a tela não alarma à toa.
        concedidas = ig.permissoes_concedidas(longo) or ig.permissoes_concedidas(curto)
        conexao = self.repo.upsert_connection(
            user_id=user_id,
            ig_user_id=ig_user_id,
            ig_username=perfil.get("username"),
            ig_avatar_url=perfil.get("profile_picture_url"),
            account_type=tipo_conta or None,
            access_token_encrypted=encrypt_value(access_token),
            token_expires_at=token_expires_at,
            scopes=",".join(concedidas or ig.DEFAULT_SCOPES),
        )
        if concedidas and ig.ESCOPO_COMENTARIOS not in concedidas:
            logger.warning(
                "Instagram: user_id=%s conectou SEM %s — resposta pública no "
                "comentário não vai funcionar. Concedidas: %s",
                user_id, ig.ESCOPO_COMENTARIOS, ",".join(concedidas),
            )
        self.db.commit()
        self.db.refresh(conexao)

        # Passo 3 de 4 da Meta: sem isto o webhook NUNCA dispara, em silêncio.
        await self.assinar_webhook(conexao)
        self.db.refresh(conexao)

        logger.info(
            "Instagram conectado user_id=%s @%s (expira em %s, webhook=%s)",
            user_id, conexao.ig_username, token_expires_at, conexao.webhook_subscrito,
        )
        return self._montar_resposta(conexao)

    def disconnect(self, user_id: int) -> None:
        """Remove a conexão e tudo que depende dela. Idempotente."""
        removida = self.repo.delete_connection(user_id)
        self.db.commit()
        logger.info(
            "Instagram desconectado user_id=%s (%s)",
            user_id, "removido" if removida else "nada a remover",
        )

    def handle_token_invalido(self, conexao: InstagramConnection) -> None:
        """Token recusado pela Meta durante um envio (código 190).

        Marca a conexão como expirada e pausa as automações NA HORA. Sem isso, os
        comentários seguintes falhariam um a um até alguém olhar o log — e a
        aluna só descobriria pelas alunas dela reclamando que não chegou link.
        """
        if conexao.status == CONEXAO_EXPIRADA:
            return
        conexao.status = CONEXAO_EXPIRADA
        pausadas = self.repo.pause_all_for_connection(conexao.id)
        self.db.commit()
        logger.error(
            "Instagram: token recusado no envio user_id=%s — %d automações pausadas",
            conexao.user_id, pausadas,
        )

    # --------------------------- webhook por conta ----------------------- #

    async def assinar_webhook(self, conexao: InstagramConnection) -> bool:
        """Inscreve a conta nas notificações de comentário (passo 3 de 4 da Meta).

        Guarda o resultado em `webhook_subscrito`/`webhook_erro`. NÃO levanta
        exceção: falhar aqui não pode derrubar a conexão inteira.

        Por que não bloquear a conexão quando falha: a doc da Meta não diz se esta
        chamada funciona com o app em Development mode. Se não funcionar, bloquear
        tornaria IMPOSSÍVEL conectar antes do App Review — e é justamente em
        homologação que a gente precisa conectar. Então gravamos a conexão, marcamos
        o problema e deixamos a tela avisar, com botão de tentar de novo.
        """
        try:
            ok = await ig.subscribe_account_to_webhooks(
                self.token_de(conexao), conexao.ig_user_id
            )
        except ig.InstagramApiError as exc:
            conexao.webhook_subscrito = False
            conexao.webhook_erro = exc.mensagem[:2000]
            self.db.commit()
            logger.error(
                "Instagram: conta %s NÃO inscrita no webhook (%s) — automações não vão disparar",
                conexao.ig_user_id, exc.mensagem,
            )
            return False

        conexao.webhook_subscrito = bool(ok)
        conexao.webhook_subscrito_em = _agora() if ok else None
        conexao.webhook_erro = None if ok else "A Meta não confirmou a inscrição."
        self.db.commit()
        logger.info(
            "Instagram: conta %s inscrita no webhook de comentários (%s)",
            conexao.ig_user_id, ok,
        )
        return bool(ok)

    async def assinar_webhook_do_usuario(self, user_id: int) -> InstagramConnectionResponse:
        """Retentativa manual, pelo botão da tela."""
        conexao = self.repo.get_connection_by_user(user_id)
        if not conexao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Nenhuma conta do Instagram conectada.",
            )
        await self.assinar_webhook(conexao)
        self.db.refresh(conexao)
        resp = self._montar_resposta(conexao)
        resp.status = self._resolver_status(conexao)
        return resp

    async def garantir_webhook(self, user_id: int) -> InstagramConnection:
        """Devolve a conexão com a inscrição em dia, tentando reparar na hora.

        Usada antes de ATIVAR uma automação: se a conta não está inscrita, tenta
        inscrever agora. Auto-reparo em vez de mandar a aluna para outra tela.
        """
        conexao = self.require_conexao_ativa(user_id)
        if not conexao.webhook_subscrito:
            await self.assinar_webhook(conexao)
            self.db.refresh(conexao)
        return conexao

    # --------------------------- deauthorize ---------------------------- #

    def handle_deauthorize(self, ig_user_id: str) -> bool:
        """A aluna removeu o app pelo Instagram (Apps e sites).

        A conexão vira `revogado` e as automações são pausadas — mas nada é
        apagado. Se ela reconectar, reencontra tudo como deixou.
        """
        conexao = self.repo.get_connection_by_ig_user_id(ig_user_id)
        if not conexao:
            logger.info("Deauthorize do Instagram para conta desconhecida: %s", ig_user_id)
            return False
        conexao.status = CONEXAO_REVOGADA
        pausadas = self.repo.pause_all_for_connection(conexao.id)
        self.db.commit()
        logger.warning(
            "Instagram revogado pelo usuário ig_user_id=%s user_id=%s — %d automações pausadas",
            ig_user_id, conexao.user_id, pausadas,
        )
        return True

    def handle_data_deletion(self, ig_user_id: str) -> Optional[int]:
        """Pedido de exclusão de dados. Apaga conexão, automações e eventos.

        Devolve o user_id afetado (ou None), para o endpoint montar a URL de
        acompanhamento no formato que a Meta exige.
        """
        conexao = self.repo.get_connection_by_ig_user_id(ig_user_id)
        if not conexao:
            return None
        user_id = conexao.user_id
        self.repo.delete_connection(user_id)
        self.db.commit()
        logger.warning("Instagram: dados apagados a pedido da Meta user_id=%s", user_id)
        return user_id

    # ---------------------------- renovação ----------------------------- #

    async def refresh_connection(self, conexao: InstagramConnection) -> bool:
        """Renova o token longo por mais 60 dias. True se renovou."""
        try:
            resposta = await ig.refresh_long_lived_token(self.token_de(conexao))
        except ig.InstagramApiError as exc:
            if exc.permanente:
                # Token já morto ou revogado: renovar não resolve. Marca e pausa,
                # pra aluna ver o alerta em vez de a automação falhar em silêncio.
                conexao.status = CONEXAO_EXPIRADA
                self.repo.pause_all_for_connection(conexao.id)
                self.db.commit()
                logger.error(
                    "Instagram: renovação impossível user_id=%s (%s) — automações pausadas",
                    conexao.user_id, exc.mensagem,
                )
                return False
            logger.warning(
                "Instagram: renovação falhou (transitório) user_id=%s: %s",
                conexao.user_id, exc.mensagem,
            )
            return False

        novo_token = resposta.get("access_token")
        expira_em = int(resposta.get("expires_in") or 0)
        if not novo_token:
            logger.warning("Instagram: renovação sem token na resposta user_id=%s", conexao.user_id)
            return False

        conexao.access_token = encrypt_value(novo_token)
        conexao.token_expires_at = _agora() + timedelta(seconds=expira_em) if expira_em else None
        conexao.last_refreshed_at = _agora()
        conexao.status = CONEXAO_ATIVA
        self.db.commit()
        logger.info(
            "Instagram: token renovado user_id=%s, nova validade %s",
            conexao.user_id, conexao.token_expires_at,
        )

        # Reinscreve por precaução. A doc NÃO diz que a inscrição cai junto com o
        # token antigo — mas também não garante que sobrevive, e a chamada é
        # idempotente e barata. Falhar aqui não desfaz a renovação, que já valeu.
        try:
            await self.assinar_webhook(conexao)
        except Exception as exc:  # nunca derrubar a renovação por causa disto
            logger.warning(
                "Instagram: reinscrição no webhook falhou após renovar user_id=%s: %s",
                conexao.user_id, exc,
            )
        return True


async def run_instagram_token_refresh_all(dias_folga: int = DIAS_FOLGA_RENOVACAO) -> dict:
    """Renova todos os tokens que vencem em menos de `dias_folga`.

    Falha de uma conta não interrompe as outras.
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    renovados = 0
    falhas = 0
    try:
        repo = InstagramAutomationRepository(db)
        svc = InstagramConnectionService(repo)
        pendentes = repo.connections_needing_refresh(dias_folga)
        for conexao in pendentes:
            try:
                if await svc.refresh_connection(conexao):
                    renovados += 1
                else:
                    falhas += 1
            except Exception as exc:
                falhas += 1
                db.rollback()
                logger.error(
                    "run_instagram_token_refresh_all: user_id=%s falhou: %s",
                    conexao.user_id, exc,
                )
        logger.info(
            "run_instagram_token_refresh_all: %d renovados, %d falhas (%d candidatos)",
            renovados, falhas, len(pendentes),
        )
        return {"renovados": renovados, "falhas": falhas, "candidatos": len(pendentes)}
    finally:
        db.close()
