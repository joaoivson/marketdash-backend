"""Anúncios na tela de seleção: mídias descobertas pelo comentário (migration 085).

Por que existe (17/09/2026, @promosdabeatrizz_): a publicação tinha 15 comentários
e a aluna via "50+" — os outros ~200 estavam em ANÚNCIOS. Anúncio criado no
Gerenciador é outra mídia, com outro id, e não aparece em GET /me/media. Sem ele
na tela, não havia como criar automação para ele.

O modelo é o da InstaMagic: a mídia aparece depois do PRIMEIRO comentário, com a
tag "Anúncio", e a automação é configurada nela como em qualquer post. O pipeline
já registra a mídia comentada; aqui ela é conferida e enfeitada para a tela:

1. **É mesmo anúncio?** `ad_id` no webhook é certeza. Sem ele, a mídia é
   comparada com a lista de publicações orgânicas (cache de 15 min): fora dela,
   é anúncio. Post do feed nunca aparece como anúncio.
2. **Metadados** (legenda, miniatura, link) vêm de GET /{media_id} com o token da
   própria conta. Se a Meta não deixar ler, a mídia aparece assim mesmo, com o
   título do anúncio e a contagem de comentários — melhor que sumir.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.instagram_automation import InstagramConnection, InstagramMidiaDetectada
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import InstagramMediaItem, InstagramMediaPage
from app.services import instagram_login_client as ig
from app.services.instagram_automation_service import InstagramAutomationService, _caption_preview
from app.services.instagram_connection_service import InstagramConnectionService
from app.services.instagram_media_url import expirada as thumbnail_expirada
from app.utils.text_normalize import palavra_pedida_na_legenda

logger = logging.getLogger(__name__)

# Teto de páginas de /me/media lidas para conferir o que é orgânico. A conta do
# caso tinha 298 publicações = 13 páginas; 40 cobre contas bem maiores.
MAX_PAGINAS_ORGANICAS = 40

# Leitura de metadados por abertura de tela: é enfeite, não pode segurar a lista.
MAX_METADADOS_POR_CARGA = 20
TIMEOUT_METADADOS_SEGUNDOS = 8.0

# Mídia cuja leitura falhou só é tentada de novo depois disto.
RETENTAR_METADADOS_APOS = timedelta(hours=6)


def _precisa_metadados(midia: InstagramMidiaDetectada, agora: datetime) -> bool:
    if midia.metadados_lidos_em is None:
        return True
    lido = midia.metadados_lidos_em
    if lido.tzinfo is None:
        lido = lido.replace(tzinfo=timezone.utc)
    if midia.metadados_erro:
        return agora - lido > RETENTAR_METADADOS_APOS
    # Leu antes e a miniatura (URL assinada do CDN) venceu: relê.
    return bool(midia.thumbnail_url) and thumbnail_expirada(midia.thumbnail_url)


def _item(midia: InstagramMidiaDetectada, cobertos: frozenset) -> InstagramMediaItem:
    miniatura = midia.thumbnail_url
    if miniatura and thumbnail_expirada(miniatura):
        miniatura = None
    return InstagramMediaItem(
        id=midia.media_id,
        caption_preview=_caption_preview(midia.caption) or midia.ad_title,
        media_type=midia.media_type,
        media_product_type=midia.media_product_type,
        permalink=midia.permalink,
        thumbnail_url=miniatura,
        timestamp=midia.media_timestamp
        or (midia.ultimo_comentario_em.isoformat() if midia.ultimo_comentario_em else None),
        tem_automacao=midia.media_id in cobertos,
        palavra_sugerida=palavra_pedida_na_legenda(midia.caption),
        eh_anuncio=True,
        ad_title=midia.ad_title,
        comentarios=midia.comentarios or 0,
        ultimo_comentario_em=midia.ultimo_comentario_em,
    )


class InstagramAnunciosService:
    def __init__(self, repo: InstagramAutomationRepository):
        self.repo = repo
        self.db: Session = repo.db
        self.conexao_service = InstagramConnectionService(repo)

    async def listar(self, user_id: int) -> InstagramMediaPage:
        conexao = self.conexao_service.require_conexao_ativa(user_id)
        midias = self.repo.midias_detectadas(conexao.id)

        pendentes = [m for m in midias if m.eh_anuncio is None]
        if pendentes:
            organicas = await self._ids_organicos(user_id)
            if organicas is not None:
                for midia in pendentes:
                    midia.eh_anuncio = midia.media_id not in organicas
                self.db.commit()

        anuncios = [m for m in midias if m.eh_anuncio is True]
        await self._ler_metadados(conexao, anuncios)

        cobertos = self.repo.media_ids_com_automacao_ativa(conexao.id)
        return InstagramMediaPage(
            items=[_item(m, cobertos) for m in anuncios], next_cursor=None, from_cache=False
        )

    async def _ids_organicos(self, user_id: int) -> Optional[set]:
        """Ids de TODAS as publicações do feed, pela mesma listagem cacheada da grade.

        None quando a leitura falha no meio: sem a lista inteira não dá para
        afirmar que uma mídia está fora dela, e marcar post do feed como anúncio
        seria pior que esperar a próxima abertura.
        """
        automacoes = InstagramAutomationService(self.repo)
        ids: set = set()
        cursor: Optional[str] = None
        for _ in range(MAX_PAGINAS_ORGANICAS):
            try:
                pagina = await automacoes.listar_midias(user_id, cursor=cursor)
            except HTTPException as exc:
                logger.warning(
                    "Instagram anúncios: não li as orgânicas do user_id=%s: %s", user_id, exc.detail
                )
                return None
            ids.update(item.id for item in pagina.items)
            cursor = pagina.next_cursor
            if not cursor:
                return ids
        logger.warning("Instagram anúncios: user_id=%s passou de %d páginas", user_id, MAX_PAGINAS_ORGANICAS)
        return None

    async def _ler_metadados(
        self, conexao: InstagramConnection, midias: List[InstagramMidiaDetectada]
    ) -> None:
        """Legenda/miniatura/link pela Graph. Erro nunca propaga nem pausa nada.

        Mesma regra da renovação de thumbnail: código 190 aqui NÃO chama
        handle_token_invalido — abrir uma tela não pode pausar as automações.
        """
        agora = datetime.now(timezone.utc)
        alvos = [m for m in midias if _precisa_metadados(m, agora)][:MAX_METADADOS_POR_CARGA]
        if not alvos:
            return
        try:
            token = self.conexao_service.token_de(conexao)
            resultados = await asyncio.wait_for(
                asyncio.gather(*(ig.get_media(token, m.media_id) for m in alvos), return_exceptions=True),
                timeout=TIMEOUT_METADADOS_SEGUNDOS,
            )
        except Exception as exc:
            logger.warning("Instagram anúncios: metadados não lidos (conexão %s): %s", conexao.id, exc)
            return

        for midia, resultado in zip(alvos, resultados):
            midia.metadados_lidos_em = agora
            if isinstance(resultado, BaseException):
                midia.metadados_erro = str(getattr(resultado, "mensagem", resultado))[:1000]
                continue
            midia.metadados_erro = None
            midia.caption = resultado.get("caption")
            midia.permalink = resultado.get("permalink")
            midia.thumbnail_url = resultado.get("thumbnail_url") or resultado.get("media_url")
            midia.media_type = resultado.get("media_type")
            midia.media_product_type = resultado.get("media_product_type")
            midia.media_timestamp = resultado.get("timestamp")
        self.db.commit()

    # -------------------------------- admin -------------------------------- #

    async def inspecionar_admin(self, media_id: str) -> dict:
        """O que a Graph devolve para uma mídia comentada — só leitura, sem texto de comentário.

        Existe para responder, com dado, "a Meta deixa ler mídia de anúncio com o
        token do Instagram Login?". Acha a conta pela mídia detectada.
        """
        midia = (
            self.db.query(InstagramMidiaDetectada)
            .filter(InstagramMidiaDetectada.media_id == str(media_id))
            .first()
        )
        if midia is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mídia não detectada.")
        conexao = self.db.get(InstagramConnection, midia.connection_id)
        if conexao is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conexão não encontrada.")
        token = self.conexao_service.token_de(conexao)

        saida: dict = {
            "media_id": midia.media_id,
            "user_id": midia.user_id,
            "registro": {
                "eh_anuncio": midia.eh_anuncio,
                "ad_id": midia.ad_id,
                "ad_title": midia.ad_title,
                "original_media_id": midia.original_media_id,
                "comentarios": midia.comentarios,
                "ultimo_comentario_em": midia.ultimo_comentario_em,
            },
        }
        try:
            bruto = await ig.get_media(token, midia.media_id)
            saida["graph_midia"] = {
                k: bruto.get(k)
                for k in ("id", "media_type", "media_product_type", "permalink", "timestamp")
            }
            saida["graph_midia"]["tem_legenda"] = bool(bruto.get("caption"))
            saida["graph_midia"]["legenda_inicio"] = (bruto.get("caption") or "")[:80]
            saida["graph_midia"]["tem_miniatura"] = bool(bruto.get("thumbnail_url") or bruto.get("media_url"))
        except ig.InstagramApiError as exc:
            saida["graph_midia_erro"] = {"codigo": exc.codigo, "mensagem": exc.mensagem}
        try:
            pagina = await ig.list_comments(token, midia.media_id, limit=50)
            dados = pagina.get("data") or []
            saida["graph_comentarios"] = {
                "na_primeira_pagina": len(dados),
                "tem_mais": bool((pagina.get("paging") or {}).get("next")),
                "mais_recente": max((c.get("timestamp") or "" for c in dados), default=None),
            }
        except ig.InstagramApiError as exc:
            saida["graph_comentarios_erro"] = {"codigo": exc.codigo, "mensagem": exc.mensagem}
        return saida
