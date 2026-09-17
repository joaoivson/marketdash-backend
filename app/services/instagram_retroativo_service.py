"""Envio retroativo: direct para quem comentou e não recebeu.

Por que existe (17/09/2026, automação 12 de @promosdabeatrizz_): o post tinha
50+ comentários e a tela mostrava 4 directs. Comentário que não gerou direct na
hora — webhook que não chegou, automação criada depois do comentário, anúncio
que a Meta entregava com outro `media.id` — ficava sem resposta para sempre.

Dois limites que moldam tudo aqui:

- **A Meta só aceita private reply até 7 dias depois do comentário.** Não há
  retroativo além disso, por nenhum caminho. A prévia separa esses comentários
  num grupo próprio para a aluna ver que é regra da Meta, não defeito.
- **O envio passa pelo MESMO pipeline do webhook** (task de comentário). Dedupe
  por comment_id e por pessoa, janela, teto horário e resposta pública valem
  igual — um retroativo nunca pode fazer o que o caminho normal não faria.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, List, Optional, Set

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.cache import get_client
from app.models.instagram_automation import (
    AUTOMACAO_ATIVA,
    CONEXAO_ATIVA,
    DM_ENVIADO,
    ESCOPO_POST_ESPECIFICO,
    InstagramAutomation,
    InstagramConnection,
)
from app.repositories.instagram_automation_repository import InstagramAutomationRepository
from app.schemas.instagram_automation import InstagramRetroativoEnvio, InstagramRetroativoPrevia
from app.services import instagram_login_client as ig
from app.services.instagram_comment_pipeline import (
    JANELA_PRIVATE_REPLY_DIAS,
    automacao_dispara,
    dentro_da_janela,
    parse_comment_timestamp,
)
from app.services.instagram_connection_service import InstagramConnectionService

logger = logging.getLogger(__name__)

# Teto de leitura. Post viral tem milhares de comentários e a prévia é um request
# síncrono: acima disto a resposta avisa `truncado` em vez de segurar a tela.
MAX_COMENTARIOS_ANALISADOS = 2000

# Espaçamento entre os directs enfileirados. Cinquenta envios no mesmo segundo é
# exatamente o padrão que faz o Instagram tratar a conta como bot.
INTERVALO_ENTRE_ENVIOS_S = 3

# Trava contra clique duplo. O pipeline já não manda duas vezes (comment_id é
# UNIQUE), mas enfileirar tudo de novo gastaria fila e chamadas à toa.
TRAVA_ENVIO_SEGUNDOS = 300

GRUPO_ELEGIVEL = "elegivel"
GRUPO_JA_RESPONDIDO = "ja_respondido"
GRUPO_JA_PROCESSADO = "ja_processado"
GRUPO_SEM_PALAVRA = "sem_palavra"
GRUPO_PESSOA_JA_RECEBEU = "pessoa_ja_recebeu"
GRUPO_FORA_DA_JANELA = "fora_da_janela"
GRUPO_PROPRIA_CONTA = "da_propria_conta"


@dataclass
class ComentarioClassificado:
    comment_id: str
    commenter_id: str
    username: Optional[str]
    texto: str
    timestamp: Optional[datetime]
    bruto_timestamp: Optional[str]
    grupo: str


def achatar_comentarios(pagina: Iterable[dict]) -> List[dict]:
    """Comentários de primeiro nível + respostas, numa lista só."""
    saida: List[dict] = []
    for comentario in pagina or []:
        saida.append(comentario)
        for resposta in ((comentario.get("replies") or {}).get("data")) or []:
            saida.append(resposta)
    return saida


def _eh_da_propria_conta(comentario: dict, conexao: InstagramConnection) -> bool:
    de = comentario.get("from") or {}
    if de.get("id") and str(de.get("id")) == str(conexao.ig_user_id):
        return True
    # A API nem sempre devolve `from`; o username é o que sobra para não mandar
    # direct para a própria aluna (inclusive para as respostas públicas NOSSAS).
    username = comentario.get("username") or de.get("username")
    return bool(
        username and conexao.ig_username and username.lower() == conexao.ig_username.lower()
    )


def classificar_comentarios(
    comentarios: List[dict],
    automacao: InstagramAutomation,
    conexao: InstagramConnection,
    status_por_comentario: dict,
    pessoas_que_receberam: Set[str],
    agora: Optional[datetime] = None,
) -> List[ComentarioClassificado]:
    """Cada comentário num grupo, e só num.

    Ordem cronológica de propósito: se a mesma pessoa comentou três vezes, quem
    recebe é o PRIMEIRO comentário dela — o que o webhook teria respondido.
    Comentário fora da janela ou sem a palavra não "gasta" a pessoa: o seguinte
    dela, se servir, ainda entra.
    """
    agora = agora or datetime.now(timezone.utc)
    pessoas = set(pessoas_que_receberam)
    minimo = datetime.min.replace(tzinfo=timezone.utc)

    enriquecidos = [
        (c, parse_comment_timestamp(c.get("timestamp")))
        for c in comentarios
        if c.get("id")
    ]
    enriquecidos.sort(key=lambda par: par[1] or minimo)

    saida: List[ComentarioClassificado] = []
    for comentario, ts in enriquecidos:
        comment_id = str(comentario["id"])
        de = comentario.get("from") or {}
        commenter_id = str(de.get("id") or "")
        texto = comentario.get("text") or ""
        status_evento = status_por_comentario.get(comment_id)

        if _eh_da_propria_conta(comentario, conexao):
            grupo = GRUPO_PROPRIA_CONTA
        elif status_evento == DM_ENVIADO:
            grupo = GRUPO_JA_RESPONDIDO
        elif status_evento:
            # sem_match, duplicado, falhou, expirado… o pipeline já decidiu, e
            # reenfileirar daria "comment_id já processado" do mesmo jeito.
            grupo = GRUPO_JA_PROCESSADO
        elif not automacao_dispara(automacao, texto):
            grupo = GRUPO_SEM_PALAVRA
        elif commenter_id and commenter_id in pessoas:
            grupo = GRUPO_PESSOA_JA_RECEBEU
        elif ts is None or not dentro_da_janela(ts, agora):
            # Sem timestamp o webhook tenta; aqui não: a leitura pela API sempre
            # traz o campo, e a falta dele é anomalia, não motivo para arriscar.
            grupo = GRUPO_FORA_DA_JANELA
        else:
            grupo = GRUPO_ELEGIVEL
            if commenter_id:
                pessoas.add(commenter_id)

        saida.append(
            ComentarioClassificado(
                comment_id=comment_id,
                commenter_id=commenter_id,
                username=comentario.get("username") or de.get("username"),
                texto=texto,
                timestamp=ts,
                bruto_timestamp=comentario.get("timestamp"),
                grupo=grupo,
            )
        )
    return saida


def montar_previa(
    automation_id: int, classificados: List[ComentarioClassificado], truncado: bool
) -> InstagramRetroativoPrevia:
    contagem = {}
    for c in classificados:
        contagem[c.grupo] = contagem.get(c.grupo, 0) + 1

    elegiveis_ts = [c.timestamp for c in classificados if c.grupo == GRUPO_ELEGIVEL and c.timestamp]
    return InstagramRetroativoPrevia(
        automation_id=automation_id,
        total_comentarios=len(classificados),
        elegiveis=contagem.get(GRUPO_ELEGIVEL, 0),
        ja_respondidos=contagem.get(GRUPO_JA_RESPONDIDO, 0),
        ja_processados=contagem.get(GRUPO_JA_PROCESSADO, 0),
        sem_palavra=contagem.get(GRUPO_SEM_PALAVRA, 0),
        pessoa_ja_recebeu=contagem.get(GRUPO_PESSOA_JA_RECEBEU, 0),
        fora_da_janela=contagem.get(GRUPO_FORA_DA_JANELA, 0),
        da_propria_conta=contagem.get(GRUPO_PROPRIA_CONTA, 0),
        truncado=truncado,
        primeiro_expira_em=(
            min(elegiveis_ts) + timedelta(days=JANELA_PRIVATE_REPLY_DIAS) if elegiveis_ts else None
        ),
    )


class InstagramRetroativoService:
    def __init__(self, repo: InstagramAutomationRepository):
        self.repo = repo
        self.db: Session = repo.db
        self.conexao_service = InstagramConnectionService(repo)

    # ------------------------------ leitura ------------------------------ #

    def _automacao_do_post(self, user_id: int, automation_id: int) -> InstagramAutomation:
        automacao = self.repo.get_automation(user_id, automation_id)
        if not automacao:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Automação não encontrada."
            )
        if automacao.escopo != ESCOPO_POST_ESPECIFICO or not automacao.media_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "O envio retroativo existe só para automação de uma publicação "
                    "específica."
                ),
            )
        return automacao

    def _conexao_da_automacao(self, automacao: InstagramAutomation) -> InstagramConnection:
        conexao = self.repo.get_connection_by_user(automacao.user_id)
        if not conexao or conexao.id != automacao.connection_id or conexao.status != CONEXAO_ATIVA:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Reconecte o Instagram em Configurações para enviar retroativos.",
            )
        return conexao

    async def ler_comentarios(
        self, conexao: InstagramConnection, media_id: str
    ) -> tuple[List[dict], bool]:
        """Todos os comentários do post (até o teto), com respostas."""
        token = self.conexao_service.token_de(conexao)
        comentarios: List[dict] = []
        cursor: Optional[str] = None
        while True:
            try:
                pagina = await ig.list_comments(token, media_id, after=cursor)
            except ig.InstagramApiError as exc:
                if exc.codigo == 190:
                    self.conexao_service.handle_token_invalido(conexao)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=exc.mensagem)
            comentarios.extend(achatar_comentarios(pagina.get("data") or []))

            paging = pagina.get("paging") or {}
            cursor = (paging.get("cursors") or {}).get("after") if paging.get("next") else None
            if len(comentarios) >= MAX_COMENTARIOS_ANALISADOS:
                return comentarios[:MAX_COMENTARIOS_ANALISADOS], bool(cursor) or (
                    len(comentarios) > MAX_COMENTARIOS_ANALISADOS
                )
            if not cursor:
                return comentarios, False

    async def levantar(
        self, user_id: int, automation_id: int
    ) -> tuple[InstagramAutomation, InstagramConnection, List[ComentarioClassificado], bool]:
        automacao = self._automacao_do_post(user_id, automation_id)
        conexao = self._conexao_da_automacao(automacao)
        comentarios, truncado = await self.ler_comentarios(conexao, automacao.media_id)
        classificados = classificar_comentarios(
            comentarios,
            automacao,
            conexao,
            self.repo.status_dos_comentarios(user_id, [str(c.get("id")) for c in comentarios]),
            self.repo.pessoas_que_receberam(user_id, automacao.id),
        )
        return automacao, conexao, classificados, truncado

    async def previa(self, user_id: int, automation_id: int) -> InstagramRetroativoPrevia:
        automacao, _, classificados, truncado = await self.levantar(user_id, automation_id)
        return montar_previa(automacao.id, classificados, truncado)

    # ------------------------------- envio ------------------------------- #

    async def enviar(self, user_id: int, automation_id: int) -> InstagramRetroativoEnvio:
        """Recalcula a prévia NO SERVIDOR e enfileira só os elegíveis.

        Não confia em lista vinda da tela: entre a prévia e o clique, o webhook
        pode ter respondido alguém, e a prévia pode ter horas.
        """
        automacao = self._automacao_do_post(user_id, automation_id)
        if automacao.status != AUTOMACAO_ATIVA:
            # Pausada, o pipeline descarta o comentário como "nenhuma automação
            # cobre este post" — enfileirar seria mandar a aluna esperar por nada.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Ative a automação para enviar os directs retroativos.",
            )
        if not _adquirir_trava(automation_id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Um envio retroativo desta automação já está em andamento.",
            )

        try:
            automacao, conexao, classificados, truncado = await self.levantar(
                user_id, automation_id
            )
        except Exception:
            # Falhou ANTES de enfileirar: a trava não protege nada e prenderia a
            # aluna 5 minutos sem poder tentar de novo.
            _soltar_trava(automation_id)
            raise
        elegiveis = [c for c in classificados if c.grupo == GRUPO_ELEGIVEL]

        from app.tasks.instagram_tasks import processar_comentario_instagram_task

        enfileirados = 0
        for indice, comentario in enumerate(elegiveis):
            valor = {
                "id": comentario.comment_id,
                "text": comentario.texto,
                "from": {"id": comentario.commenter_id, "username": comentario.username},
                "media": {"id": automacao.media_id},
                "timestamp": comentario.bruto_timestamp,
            }
            try:
                # priority=9: é lote, não pode furar a fila do webhook ao vivo.
                # Só 0 e 9 são consumidos (ver .claude/rules/celery-filas.md).
                processar_comentario_instagram_task.apply_async(
                    kwargs={"ig_user_id": conexao.ig_user_id, "valor": valor, "entrega_id": None},
                    priority=9,
                    countdown=indice * INTERVALO_ENTRE_ENVIOS_S,
                )
                enfileirados += 1
            except Exception as exc:
                logger.error(
                    "Instagram retroativo: falha ao enfileirar comment=%s automacao=%s: %s",
                    comentario.comment_id, automacao.id, exc,
                )

        logger.info(
            "Instagram retroativo: automacao=%s user_id=%s elegiveis=%d enfileirados=%d",
            automacao.id, user_id, len(elegiveis), enfileirados,
        )
        return InstagramRetroativoEnvio(
            enfileirados=enfileirados,
            previa=montar_previa(automacao.id, classificados, truncado),
        )


def _adquirir_trava(automation_id: int) -> bool:
    """Sem Redis, libera: o pipeline continua sem mandar duas vezes."""
    cliente = get_client()
    if cliente is None:
        return True
    try:
        return bool(
            cliente.set(
                f"ig:retroativo:{automation_id}", "1", nx=True, ex=TRAVA_ENVIO_SEGUNDOS
            )
        )
    except Exception as exc:
        logger.warning("Instagram retroativo: trava indisponível (%s) — seguindo sem ela", exc)
        return True


def _soltar_trava(automation_id: int) -> None:
    cliente = get_client()
    if cliente is None:
        return
    try:
        cliente.delete(f"ig:retroativo:{automation_id}")
    except Exception as exc:
        logger.warning("Instagram retroativo: falha ao soltar a trava (%s)", exc)
