"""CRUD das automações + listagem de publicações com cache."""

import logging
import time
from typing import Dict, List, Optional, Tuple

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    ESCOPO_POST_ESPECIFICO,
    ESCOPO_STORY_ESPECIFICO,
    ESCOPO_STORY_QUALQUER,
    ESCOPOS_DE_STORY,
    ESCOPO_QUALQUER,
    TRIGGER_PALAVRAS,
    InstagramAutomation,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import (
    InstagramAutomacaoLoteItem,
    InstagramAutomacaoLotePulada,
    InstagramAutomacaoLoteRequest,
    InstagramAutomacaoLoteResponse,
    InstagramAutomationCreate,
    InstagramAutomationResponse,
    InstagramAutomationUpdate,
    InstagramMediaItem,
    InstagramMediaPage,
)
from app.services import instagram_login_client as ig
from app.services.instagram_connection_service import InstagramConnectionService
from app.utils.text_normalize import normalizar_comentario, palavra_pedida_na_legenda

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


def _media_item(bruto: dict, cobertos: frozenset[str] = frozenset()) -> InstagramMediaItem:
    media_id = str(bruto.get("id") or "")
    legenda = bruto.get("caption")
    return InstagramMediaItem(
        id=media_id,
        caption_preview=_caption_preview(legenda),
        media_type=bruto.get("media_type"),
        media_product_type=bruto.get("media_product_type"),
        permalink=bruto.get("permalink"),
        # Vídeo/Reel só tem thumbnail_url; imagem só tem media_url.
        thumbnail_url=bruto.get("thumbnail_url") or bruto.get("media_url"),
        timestamp=bruto.get("timestamp"),
        tem_automacao=media_id in cobertos,
        # A sugestão sai da legenda INTEIRA, não do preview: o preview é cortado
        # para caber na tela e o pedido costuma vir depois do corte.
        palavra_sugerida=palavra_pedida_na_legenda(legenda),
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

        # Fora do cache DE PROPÓSITO: o cache guarda a resposta da Meta (15 min),
        # mas a cobertura muda no segundo em que a aluna cria uma automação. Ler
        # do banco a cada chamada é uma query e evita a tela dizer "sem
        # automação" para um post que ela acabou de cobrir.
        cobertos = self.repo.media_ids_com_automacao_ativa(conexao.id)

        if not forcar:
            em_cache = _CACHE_MEDIA.get(chave)
            if em_cache and (time.time() - em_cache[0]) < CACHE_MEDIA_TTL_SEGUNDOS:
                bruto = em_cache[1]
                return InstagramMediaPage(
                    items=[_media_item(m, cobertos) for m in bruto.get("data") or []],
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
            items=[_media_item(m, cobertos) for m in bruto.get("data") or []],
            next_cursor=(paging.get("cursors") or {}).get("after") if paging.get("next") else None,
            from_cache=False,
        )

    async def listar_stories(self, user_id: int) -> InstagramMediaPage:
        """Stories ATIVOS (últimas 24h) para o seletor da automação de story.

        Sem cache: a lista muda ao longo do dia e é curta — buscar sempre é
        mais barato que explicar um story fantasma no seletor.
        """
        conexao = self.conexao_service.require_conexao_ativa(user_id)
        try:
            bruto = await ig.list_stories(self.conexao_service.token_de(conexao))
        except ig.InstagramApiError as exc:
            if exc.codigo == 190:
                self.conexao_service.handle_token_invalido(conexao)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.mensagem)
        return InstagramMediaPage(
            items=[_media_item(m) for m in bruto.get("data") or []],
            next_cursor=None,
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
        if dados.escopo == ESCOPO_STORY_ESPECIFICO and not dados.media_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Escolha o story em que a automação vai funcionar.",
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
        if dados.escopo in ESCOPOS_DE_STORY:
            # Story não tem comentário público — a resposta pública não se
            # aplica e é forçada a False em _aplicar; não exigir variações.
            return
        if dados.resposta_publica_ativa and not dados.resposta_publica_variacoes:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Escreva ao menos uma variação de resposta pública, ou desligue a opção.",
            )
        # Link e botão andam juntos: link sem título vira mensagem sem botão (o
        # link volta pro corpo, calado), e título sem link vira botão sem destino.
        link = (getattr(dados, "dm_link", None) or "").strip()
        botao = (getattr(dados, "dm_botao_texto", None) or "").strip()
        if link and not botao:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Escreva o texto do botão — é ele que a pessoa vê no direct.",
            )
        if botao and not link:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Informe o link para onde o botão vai levar.",
            )

    def _aplicar(self, automacao: InstagramAutomation, dados) -> None:
        automacao.dm_link = (getattr(dados, "dm_link", None) or "").strip() or None
        automacao.dm_botao_texto = (
            (getattr(dados, "dm_botao_texto", None) or "").strip()[:20] or None
        )
        automacao.nome = dados.nome.strip()
        automacao.escopo = dados.escopo
        # Os escopos 'qualquer'/'story_qualquer' não apontam pra mídia nenhuma —
        # guardar um media_id aqui faria cobre_media/cobre_story responder pela
        # mídia errada. No story_especifico, media_id guarda o ID DO STORY.
        com_midia = dados.escopo in (ESCOPO_POST_ESPECIFICO, ESCOPO_STORY_ESPECIFICO)
        automacao.media_id = dados.media_id if com_midia else None
        automacao.media_thumbnail_url = dados.media_thumbnail_url if com_midia else None
        automacao.media_caption_preview = dados.media_caption_preview if com_midia else None
        automacao.media_permalink = dados.media_permalink if com_midia else None

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

        if dados.escopo in ESCOPOS_DE_STORY:
            # Story não tem comentário público para responder.
            automacao.resposta_publica_ativa = False
            automacao.resposta_publica_variacoes = []
        else:
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

    async def criar_em_lote(
        self, user_id: int, pedido: InstagramAutomacaoLoteRequest
    ) -> InstagramAutomacaoLoteResponse:
        """Cria uma automação de post para cada item, com um modelo comum.

        Por que existe: `escopo = post_especifico` cobre UM post, e a conta que
        motivou isto tem 283 publicações pedindo "Comente X" com 9 cobertas.
        Uma a uma, pela tela de edição, ela nunca alcança — e post antigo segue
        recebendo comentário por meses.

        Três decisões:

        - **Post já coberto é PULADO, não duplicado.** Duas automações ativas no
          mesmo post disputariam o mesmo comentário, e a Meta só aceita uma
          private reply — a segunda viraria erro permanente. Também torna o
          endpoint seguro contra clique duplo.
        - **Item inválido não derruba o lote.** Ele volta em `puladas` com o
          motivo, e o resto é criado. Recusar tudo por causa de um link mal
          colado faria a aluna recomeçar a passada inteira.
        - **O webhook é conferido UMA vez**, não por item — são 50 chamadas à
          Meta que a aluna esperaria à toa.
        """
        conexao = self.conexao_service.require_conexao_ativa(user_id)
        ativar = pedido.status == AUTOMACAO_ATIVA
        if ativar:
            await self._exigir_webhook_ativo(user_id)

        ja_cobertos = self.repo.media_ids_com_automacao_ativa(conexao.id)
        vistos: set[str] = set()
        criadas: List[InstagramAutomationResponse] = []
        puladas: List[InstagramAutomacaoLotePulada] = []

        for item in pedido.itens:
            media_id = str(item.media_id)
            if media_id in ja_cobertos:
                puladas.append(InstagramAutomacaoLotePulada(
                    media_id=media_id, motivo="Este post já tem automação ativa."))
                continue
            if media_id in vistos:
                puladas.append(InstagramAutomacaoLotePulada(
                    media_id=media_id, motivo="Post repetido no mesmo lote."))
                continue

            palavras = [p for p in (list(item.palavras) + list(pedido.palavras_comuns)) if p and p.strip()]
            dados = InstagramAutomationCreate(
                nome=(item.nome or "").strip() or self._nome_do_lote(item, palavras),
                escopo=ESCOPO_POST_ESPECIFICO,
                media_id=media_id,
                media_thumbnail_url=item.media_thumbnail_url,
                media_caption_preview=item.media_caption_preview,
                media_permalink=item.media_permalink,
                trigger_tipo=TRIGGER_PALAVRAS,
                palavras=palavras,
                resposta_publica_ativa=pedido.resposta_publica_ativa,
                resposta_publica_variacoes=list(pedido.resposta_publica_variacoes),
                dm_texto=pedido.dm_texto,
                dm_link=item.dm_link,
                dm_botao_texto=pedido.dm_botao_texto,
                status=pedido.status,
            )
            try:
                self._validar(dados, para_ativar=ativar)
            except HTTPException as exc:
                puladas.append(InstagramAutomacaoLotePulada(
                    media_id=media_id, motivo=self._motivo_legivel(exc)))
                continue

            automacao = InstagramAutomation(
                user_id=user_id, connection_id=conexao.id, nome=dados.nome
            )
            self._aplicar(automacao, dados)
            self.repo.add_automation(automacao)
            vistos.add(media_id)
            criadas.append(automacao)

        # Um commit para o lote inteiro: 50 commits seriam 50 idas ao banco, e
        # um erro no meio deixaria a passada pela metade sem ninguém saber onde.
        self.db.commit()
        respostas = []
        for automacao in criadas:
            self.db.refresh(automacao)
            respostas.append(self._to_response(automacao))

        logger.info(
            "Automação Instagram em lote user_id=%s: %d criadas, %d puladas",
            user_id, len(respostas), len(puladas),
        )
        return InstagramAutomacaoLoteResponse(criadas=respostas, puladas=puladas)

    @staticmethod
    def _nome_do_lote(item: InstagramAutomacaoLoteItem, palavras: List[str]) -> str:
        """Nome legível sem pedir mais uma digitação por post.

        A palavra do produto é o melhor nome disponível — é ela que a aluna
        reconhece na lista. Sem palavra, cai no trecho da legenda.
        """
        if palavras:
            return palavras[0][:255]
        preview = (item.media_caption_preview or "").strip()
        return (preview[:60] or "Automação sem título")

    @staticmethod
    def _motivo_legivel(exc: HTTPException) -> str:
        detalhe = exc.detail
        if isinstance(detalhe, dict):
            return str(detalhe.get("message") or detalhe)
        return str(detalhe)

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
