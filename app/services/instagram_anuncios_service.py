"""Anúncios na tela de seleção: mídias descobertas pelo comentário (migration 085).

Por que existe (17/09/2026, @promosdabeatrizz_): a publicação tinha 15 comentários
e a aluna via "50+" — os outros ~200 estavam em ANÚNCIOS. Anúncio criado no
Gerenciador é outra mídia, com outro id, e não aparece em GET /me/media. Sem ele
na tela, não havia como criar automação para ele.

O modelo é o da InstaMagic: a mídia aparece depois do PRIMEIRO comentário, com a
tag "Anúncio", e a automação é configurada nela como em qualquer post. O pipeline
já registra a mídia comentada; aqui ela é conferida e enfeitada para a tela:

1. **É mesmo anúncio?** Só com prova: `ad_id` no webhook, ou a própria Graph
   dizendo `media_product_type = "AD"` em GET /{media_id}. Medido em produção
   (17/09): as 18 mídias de anúncio da conta do caso voltaram `AD`, com legenda,
   miniatura e comentários legíveis pelo token do Instagram Login.
   A primeira versão comparava com a lista de orgânicas ("fora dela é anúncio")
   e marcou como anúncio o id falso do simulador de teste — ausência não é prova.
2. **Metadados** (legenda, miniatura, link) vêm da mesma leitura. Post do feed
   lido com sucesso vira `eh_anuncio = False` e não é relido. Leitura que falhou
   fica sem veredito, some da tela e é tentada de novo mais tarde.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.instagram_automation import InstagramConnection, InstagramMidiaDetectada
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.models.instagram_automation import AUTOMACAO_ATIVA, ESCOPO_POST_ESPECIFICO
from app.schemas.instagram_automation import (
    InstagramAutomationResponse,
    InstagramMediaItem,
    InstagramMediaPage,
)
from app.services import instagram_login_client as ig
from app.services.instagram_automation_service import _caption_preview
from app.services.instagram_connection_service import InstagramConnectionService
from app.services.instagram_media_url import expirada as thumbnail_expirada
from app.utils.text_normalize import palavra_pedida_na_legenda

logger = logging.getLogger(__name__)

PRODUTO_ANUNCIO = "AD"

# Leitura de metadados por abertura de tela: é enfeite, não pode segurar a lista.
MAX_METADADOS_POR_CARGA = 20
TIMEOUT_METADADOS_SEGUNDOS = 8.0

# Mídia cuja leitura falhou só é tentada de novo depois disto.
RETENTAR_METADADOS_APOS = timedelta(hours=6)


def _eh_anuncio_comprovado(midia: InstagramMidiaDetectada) -> bool:
    return bool(midia.ad_id) or midia.media_product_type == PRODUTO_ANUNCIO


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


def _item(
    midia: InstagramMidiaDetectada, cobertos: frozenset, ativas: frozenset = frozenset()
) -> InstagramMediaItem:
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
        tem_automacao=midia.media_id in cobertos or midia.automation_id in ativas,
        palavra_sugerida=palavra_pedida_na_legenda(midia.caption),
        eh_anuncio=True,
        ad_title=midia.ad_title,
        comentarios=midia.comentarios or 0,
        ultimo_comentario_em=midia.ultimo_comentario_em,
        automation_id_vinculada=midia.automation_id,
    )


class InstagramAnunciosService:
    def __init__(self, repo: InstagramAutomationRepository):
        self.repo = repo
        self.db: Session = repo.db
        self.conexao_service = InstagramConnectionService(repo)

    async def listar(self, user_id: int) -> InstagramMediaPage:
        conexao = self.conexao_service.require_conexao_ativa(user_id)
        midias = self.repo.midias_detectadas(conexao.id)

        await self._ler_metadados(conexao, midias)
        for midia in midias:
            if _eh_anuncio_comprovado(midia):
                midia.eh_anuncio = True
            elif midia.metadados_lidos_em is not None and not midia.metadados_erro:
                # A Graph leu e disse que NÃO é anúncio: post do feed. Não volta.
                midia.eh_anuncio = False
            elif midia.eh_anuncio and not midia.ad_id:
                # Veredito antigo sem prova (regra das orgânicas): volta a ser dúvida.
                midia.eh_anuncio = None
        self.db.commit()

        anuncios = [m for m in midias if m.eh_anuncio is True]
        cobertos = self.repo.media_ids_com_automacao_ativa(conexao.id)
        ativas = frozenset(a.id for a in self.repo.active_automations_for_connection(conexao.id))
        return InstagramMediaPage(
            items=[_item(m, cobertos, ativas) for m in anuncios], next_cursor=None, from_cache=False
        )

    async def vincular(
        self, user_id: int, automation_id: int, media_ids: List[str]
    ) -> InstagramAutomationResponse:
        """Define os anúncios da automação. A lista é a COMPLETA: o resto é desvinculado.

        Um anúncio pertence a uma automação só: vincular aqui tira de onde estava.
        Vínculo com automação ATIVA passa a responder os próximos comentários do
        anúncio na hora — é a razão de existir, e por isso só a própria aluna
        (ou alguém com o aval dela) faz.
        """
        from app.services.instagram_automation_service import InstagramAutomationService

        automacao = self.repo.get_automation(user_id, automation_id)
        if not automacao:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Automação não encontrada.")
        if automacao.escopo != ESCOPO_POST_ESPECIFICO:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Só automação de uma publicação específica recebe anúncios.",
            )
        conexao = self.conexao_service.require_conexao_ativa(user_id)

        pedidos = list(dict.fromkeys(str(m) for m in media_ids if m))
        encontrados = self.repo.anuncios_da_conexao(conexao.id, pedidos)
        faltando = sorted(set(pedidos) - {m.media_id for m in encontrados})
        if faltando:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Anúncio não encontrado nesta conta: {', '.join(faltando)}",
            )

        self.repo.desvincular_anuncios(automacao.id)
        for midia in encontrados:
            midia.automation_id = automacao.id
        self.db.commit()
        logger.info(
            "Instagram: automação %s (user_id=%s) com %d anúncio(s) vinculado(s)",
            automacao.id, user_id, len(encontrados),
        )
        servico = InstagramAutomationService(self.repo)
        return servico._to_response(automacao, self.repo.contadores_por_automacao(user_id))

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
