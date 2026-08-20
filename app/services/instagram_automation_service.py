"""CRUD das automações + listagem de publicações com cache."""

import logging
import time
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_QUALQUER,
    TRIGGER_PALAVRAS,
    InstagramAutomation,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import (
    InstagramAutomationCreate,
    InstagramAutomationResponse,
    InstagramAutomationUpdate,
    InstagramMediaItem,
    InstagramMediaPage,
)
from app.services import instagram_login_client as ig
from app.services.instagram_connection_service import InstagramConnectionService
from app.utils.text_normalize import normalizar_comentario

logger = logging.getLogger(__name__)

# Cache da grade de publicações. A aluna abre a tela, rola, volta — sem cache
# cada abertura queima cota da API. 15 min é o pedido do spec §5.3.
CACHE_MEDIA_TTL_SEGUNDOS = 15 * 60
_CACHE_MEDIA: Dict[Tuple[int, str], Tuple[float, dict]] = {}

MAX_CAPTION_PREVIEW = 140


def _caption_preview(caption: Optional[str]) -> Optional[str]:
    if not caption:
        return None
    texto = " ".join(caption.split())
    return texto[:MAX_CAPTION_PREVIEW]


def _media_item(bruto: dict) -> InstagramMediaItem:
    return InstagramMediaItem(
        id=str(bruto.get("id") or ""),
        caption_preview=_caption_preview(bruto.get("caption")),
        media_type=bruto.get("media_type"),
        media_product_type=bruto.get("media_product_type"),
        permalink=bruto.get("permalink"),
        # Vídeo/Reel só tem thumbnail_url; imagem só tem media_url.
        thumbnail_url=bruto.get("thumbnail_url") or bruto.get("media_url"),
        timestamp=bruto.get("timestamp"),
    )


class InstagramAutomationService:
    def __init__(self, repo: InstagramAutomationRepository):
        self.repo = repo
        self.db: Session = repo.db
        self.conexao_service = InstagramConnectionService(repo)

    # ---------------------------- publicações ---------------------------- #

    async def listar_midias(
        self, user_id: int, cursor: Optional[str] = None, forcar: bool = False
    ) -> InstagramMediaPage:
        conexao = self.conexao_service.require_conexao_ativa(user_id)
        chave = (user_id, cursor or "")

        if not forcar:
            em_cache = _CACHE_MEDIA.get(chave)
            if em_cache and (time.time() - em_cache[0]) < CACHE_MEDIA_TTL_SEGUNDOS:
                bruto = em_cache[1]
                return InstagramMediaPage(
                    items=[_media_item(m) for m in bruto.get("data") or []],
                    next_cursor=((bruto.get("paging") or {}).get("cursors") or {}).get("after")
                    if (bruto.get("paging") or {}).get("next")
                    else None,
                    from_cache=True,
                )

        try:
            bruto = await ig.list_media(self.conexao_service.token_de(conexao), after=cursor)
        except ig.InstagramApiError as exc:
            if exc.codigo == 190:
                self.conexao_service.handle_token_invalido(conexao)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.mensagem)

        _CACHE_MEDIA[chave] = (time.time(), bruto)
        paging = bruto.get("paging") or {}
        return InstagramMediaPage(
            items=[_media_item(m) for m in bruto.get("data") or []],
            next_cursor=(paging.get("cursors") or {}).get("after") if paging.get("next") else None,
            from_cache=False,
        )

    # ----------------------------- automações ---------------------------- #

    async def _exigir_webhook_ativo(self, user_id: int) -> None:
        """Só deixa ATIVAR se a conta estiver recebendo os comentários.

        Sem isso a aluna publica o post achando que a automação está rodando, e
        descobre pelo silêncio — que é o pior jeito de descobrir. Tenta reparar
        antes de recusar: se a inscrição estiver só faltando, ela acontece aqui.
        """
        conexao = await self.conexao_service.garantir_webhook(user_id)
        if conexao.webhook_subscrito:
            return
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "WEBHOOK_NAO_ATIVO",
                "message": (
                    "Ainda não estamos recebendo os comentários deste perfil, então a "
                    "automação não dispararia. Confira em Configurações → Integração "
                    "Instagram: o perfil precisa ser público e a opção "
                    "\"Permitir acesso às mensagens\" precisa estar ligada."
                ),
                "webhook_erro": conexao.webhook_erro,
            },
        )

    def _to_response(
        self, automacao: InstagramAutomation, contadores: Optional[dict] = None
    ) -> InstagramAutomationResponse:
        resp = InstagramAutomationResponse.model_validate(automacao)
        # A tela mostra o texto ORIGINAL ("QUERO"), não o normalizado ("quero").
        resp.palavras = list(automacao.palavras_exibicao or [])
        dados = (contadores or {}).get(automacao.id) or {}
        resp.comentarios_capturados = int(dados.get("comentarios", 0))
        resp.directs_enviados = int(dados.get("directs", 0))
        return resp

    def listar(self, user_id: int) -> List[InstagramAutomationResponse]:
        contadores = self.repo.contadores_por_automacao(user_id)
        return [self._to_response(a, contadores) for a in self.repo.list_automations(user_id)]

    def obter(self, user_id: int, automation_id: int) -> InstagramAutomationResponse:
        automacao = self.repo.get_automation(user_id, automation_id)
        if not automacao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Automação não encontrada."
            )
        return self._to_response(automacao, self.repo.contadores_por_automacao(user_id))

    def _validar(self, dados, para_ativar: bool) -> None:
        """Regras que só valem quando a automação vai PUBLICAR.

        Rascunho pode ficar incompleto de propósito — a aluna salva no meio e
        volta depois. O que não pode é uma automação ativa sem palavra-chave (ela
        nunca dispararia) ou sem texto de DM (mandaria mensagem vazia).
        """
        if not para_ativar:
            return
        if dados.escopo == ESCOPO_POST_ESPECIFICO and not dados.media_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Escolha a publicação em que a automação vai funcionar.",
            )
        if dados.trigger_tipo == TRIGGER_PALAVRAS and not dados.palavras:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Informe ao menos uma palavra-chave.",
            )
        if not (dados.dm_texto or "").strip():
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Escreva a mensagem que será enviada no direct.",
            )
        if dados.resposta_publica_ativa and not dados.resposta_publica_variacoes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Escreva ao menos uma variação de resposta pública, ou desligue a opção.",
            )

    def _aplicar(self, automacao: InstagramAutomation, dados) -> None:
        automacao.nome = dados.nome.strip()
        automacao.escopo = dados.escopo
        # 'qualquer' não aponta pra post nenhum — guardar um media_id aqui faria
        # `cobre_media` responder pelo post errado.
        automacao.media_id = dados.media_id if dados.escopo == ESCOPO_POST_ESPECIFICO else None
        automacao.media_thumbnail_url = (
            dados.media_thumbnail_url if dados.escopo == ESCOPO_POST_ESPECIFICO else None
        )
        automacao.media_caption_preview = (
            dados.media_caption_preview if dados.escopo == ESCOPO_POST_ESPECIFICO else None
        )
        automacao.media_permalink = (
            dados.media_permalink if dados.escopo == ESCOPO_POST_ESPECIFICO else None
        )

        automacao.trigger_tipo = dados.trigger_tipo
        exibicao = list(dados.palavras or [])
        automacao.palavras_exibicao = exibicao
        # Normaliza UMA vez, na gravação. O matching roda a cada comentário e não
        # pode pagar normalização da configuração inteira toda vez.
        normalizadas = []
        for palavra in exibicao:
            norm = normalizar_comentario(palavra)
            if norm and norm not in normalizadas:
                normalizadas.append(norm)
        automacao.palavras = normalizadas

        automacao.resposta_publica_ativa = bool(dados.resposta_publica_ativa)
        automacao.resposta_publica_variacoes = list(dados.resposta_publica_variacoes or [])
        automacao.dm_texto = dados.dm_texto or ""
        automacao.status = dados.status

    async def criar(
        self, user_id: int, dados: InstagramAutomationCreate
    ) -> InstagramAutomationResponse:
        conexao = self.conexao_service.require_conexao_ativa(user_id)
        self._validar(dados, para_ativar=dados.status == AUTOMACAO_ATIVA)
        if dados.status == AUTOMACAO_ATIVA:
            await self._exigir_webhook_ativo(user_id)

        automacao = InstagramAutomation(user_id=user_id, connection_id=conexao.id, nome=dados.nome)
        self._aplicar(automacao, dados)
        self.repo.add_automation(automacao)
        self.db.commit()
        self.db.refresh(automacao)
        logger.info("Automação Instagram criada id=%s user_id=%s", automacao.id, user_id)
        return self._to_response(automacao)

    async def atualizar(
        self, user_id: int, automation_id: int, dados: InstagramAutomationUpdate
    ) -> InstagramAutomationResponse:
        automacao = self.repo.get_automation(user_id, automation_id)
        if not automacao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Automação não encontrada."
            )
        self._validar(dados, para_ativar=dados.status == AUTOMACAO_ATIVA)
        if dados.status == AUTOMACAO_ATIVA:
            await self._exigir_webhook_ativo(user_id)
        self._aplicar(automacao, dados)
        self.db.commit()
        self.db.refresh(automacao)
        return self._to_response(automacao, self.repo.contadores_por_automacao(user_id))

    async def alterar_status(
        self, user_id: int, automation_id: int, novo_status: str
    ) -> InstagramAutomationResponse:
        automacao = self.repo.get_automation(user_id, automation_id)
        if not automacao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Automação não encontrada."
            )
        if novo_status == AUTOMACAO_ATIVA:
            # Ligar exige conexão viva E webhook chegando: uma automação "ativa"
            # com token morto ou conta não inscrita mente pra aluna — o toggle
            # fica verde e nada é enviado.
            await self._exigir_webhook_ativo(user_id)
            faltando = self._campos_faltando(automacao)
            if faltando:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Complete a automação antes de ativar: {faltando}.",
                )
        automacao.status = novo_status
        self.db.commit()
        self.db.refresh(automacao)
        return self._to_response(automacao, self.repo.contadores_por_automacao(user_id))

    @staticmethod
    def _campos_faltando(automacao: InstagramAutomation) -> str:
        faltas = []
        if automacao.escopo == ESCOPO_POST_ESPECIFICO and not automacao.media_id:
            faltas.append("publicação")
        if automacao.trigger_tipo == TRIGGER_PALAVRAS and not (automacao.palavras or []):
            faltas.append("palavra-chave")
        if not (automacao.dm_texto or "").strip():
            faltas.append("mensagem do direct")
        if automacao.resposta_publica_ativa and not (automacao.resposta_publica_variacoes or []):
            faltas.append("resposta pública")
        return ", ".join(faltas)

    def duplicar(self, user_id: int, automation_id: int) -> InstagramAutomationResponse:
        original = self.repo.get_automation(user_id, automation_id)
        if not original:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Automação não encontrada."
            )
        copia = InstagramAutomation(
            user_id=user_id,
            connection_id=original.connection_id,
            nome=f"{original.nome} (cópia)"[:255],
            escopo=original.escopo,
            media_id=original.media_id,
            media_thumbnail_url=original.media_thumbnail_url,
            media_caption_preview=original.media_caption_preview,
            media_permalink=original.media_permalink,
            trigger_tipo=original.trigger_tipo,
            palavras=list(original.palavras or []),
            palavras_exibicao=list(original.palavras_exibicao or []),
            resposta_publica_ativa=original.resposta_publica_ativa,
            resposta_publica_variacoes=list(original.resposta_publica_variacoes or []),
            dm_texto=original.dm_texto,
            # A cópia NUNCA nasce ativa: duas automações idênticas no mesmo post
            # concorreriam pelo mesmo comentário sem a aluna ter pedido isso.
            status="pausada",
        )
        self.repo.add_automation(copia)
        self.db.commit()
        self.db.refresh(copia)
        return self._to_response(copia)

    def excluir(self, user_id: int, automation_id: int) -> None:
        automacao = self.repo.get_automation(user_id, automation_id)
        if not automacao:
            return  # idempotente
        self.repo.delete_automation(automacao)
        self.db.commit()
        logger.info("Automação Instagram excluída id=%s user_id=%s", automation_id, user_id)
