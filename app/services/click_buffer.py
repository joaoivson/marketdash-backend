"""Buffer de cliques dos links no Redis, com descarga em lote no Postgres.

Incidente de 18/09/2026 (o segundo em 24h): links com 62.480 e 23.862 cliques
faziam 1 UPDATE + 1 INSERT no Postgres POR CLIQUE. O fix de 17/09 tornou o
incremento atômico e desistiu de cliques com a linha travada, mas continuou
sendo 2 escritas por clique numa instância cujo disco cai para 5 MB/s quando
o budget de IO acaba. Numa campanha grande isso esgota o IO, e o Auth do
Supabase (que usa o mesmo banco) para de responder: login e API inteira caem.

Agora o redirecionamento NÃO escreve no banco:
- `registrar()` faz `HINCRBY` no contador do link e `RPUSH` do evento (id do
  link, id da usuária, timestamp) — duas operações O(1) no Redis;
- o primeiro clique de cada janela agenda `descarregar_cliques` no worker,
  `CLIQUES_FLUSH_INTERVALO_S` segundos depois;
- a descarga faz UM UPDATE por link (`click_count = click_count + n`) e UM
  INSERT em lote dos eventos, preservando o `created_at` original — a série
  do insight não muda.

Se o Redis estiver fora, `registrar()` devolve False e o serviço cai no
incremento atômico direto (fix de 17/09). Nada de clique perdido por
construção: se o Postgres falhar na descarga, o lote volta para o Redis.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.ambiente import identidade_do_banco
from app.core.cache import get_client
from app.core.config import settings

logger = logging.getLogger(__name__)

# As chaves levam a identidade do BANCO, como a fila do Celery (ver
# app/core/ambiente.py). Homologação e produção dividem o MESMO Redis: sem o
# prefixo, o worker de hml descarregaria clique de produção no banco de hml —
# o UPDATE não acharia a linha e o INSERT quebraria por FK, perdendo contagem.
_ID = identidade_do_banco()
CHAVE_CONTAGEM = f"cliques:{_ID}:contagem"      # hash  link_id -> n
CHAVE_EVENTOS = f"cliques:{_ID}:eventos"        # list  "link_id:user_id:epoch"
CHAVE_AGENDADO = f"cliques:{_ID}:flush:agendado"  # lock curto: 1 task por janela
LOTE_MAX_EVENTOS = 5000


@dataclass
class ResultadoDescarga:
    links_atualizados: int = 0
    eventos_gravados: int = 0
    restantes: int = 0


def registrar(link_id: int, user_id: int, *, agendar=None) -> bool:
    """Guarda 1 clique no Redis. Devolve False se o Redis não estiver disponível
    (o chamador então conta direto no banco). `agendar` é injetável nos testes;
    o padrão agenda a task Celery de descarga."""
    if not settings.CLIQUES_BUFFER_REDIS:
        return False
    client = get_client()
    if client is None:
        return False
    try:
        agora = int(time.time())
        pipe = client.pipeline(transaction=False)
        pipe.hincrby(CHAVE_CONTAGEM, str(link_id), 1)
        pipe.rpush(CHAVE_EVENTOS, f"{link_id}:{user_id}:{agora}")
        pipe.set(CHAVE_AGENDADO, "1", nx=True, ex=max(settings.CLIQUES_FLUSH_INTERVALO_S, 1))
        _, _, primeiro_da_janela = pipe.execute()
    except Exception as exc:
        logger.warning("Buffer de cliques indisponível (Redis): %s", exc)
        return False

    if primeiro_da_janela:
        try:
            (agendar or _agendar_descarga)()
        except Exception as exc:
            # A task não foi agendada: libera o lock para o próximo clique tentar.
            logger.warning("Falha ao agendar descarga de cliques: %s", exc)
            try:
                client.delete(CHAVE_AGENDADO)
            except Exception:
                pass
    return True


def _agendar_descarga() -> None:
    from app.tasks.click_tasks import descarregar_cliques

    descarregar_cliques.apply_async(countdown=settings.CLIQUES_FLUSH_INTERVALO_S)


def _parse_evento(bruto: str) -> Optional[tuple[int, int, datetime]]:
    try:
        link_id, user_id, epoch = bruto.split(":")
        return int(link_id), int(user_id), datetime.fromtimestamp(int(epoch), tz=timezone.utc)
    except (ValueError, AttributeError):
        logger.warning("Evento de clique malformado no buffer: %r", bruto)
        return None


def descarregar(db: Session) -> ResultadoDescarga:
    """Move o buffer do Redis para o Postgres em lote. Idempotente por lote:
    o lote é renomeado para uma chave própria antes de ser lido; se o banco
    falhar, volta para o buffer principal."""
    resultado = ResultadoDescarga()
    client = get_client()
    if client is None:
        return resultado

    sufixo = uuid.uuid4().hex[:8]
    chave_contagem_lote = f"{CHAVE_CONTAGEM}:lote:{sufixo}"
    chave_eventos_lote = f"{CHAVE_EVENTOS}:lote:{sufixo}"

    # Corta o lote atomicamente: cliques que chegarem durante a descarga caem
    # nas chaves principais e vão no próximo lote.
    pipe = client.pipeline(transaction=True)
    pipe.renamenx(CHAVE_CONTAGEM, chave_contagem_lote)
    pipe.renamenx(CHAVE_EVENTOS, chave_eventos_lote)
    pipe.delete(CHAVE_AGENDADO)
    try:
        pipe.execute()
    except Exception as exc:
        # renamenx numa chave inexistente levanta erro: buffer vazio.
        if "no such key" not in str(exc).lower():
            logger.warning("Falha ao cortar lote de cliques: %s", exc)
        _limpar(client, chave_contagem_lote, chave_eventos_lote)
        return resultado

    contagem = client.hgetall(chave_contagem_lote) or {}
    eventos_brutos = client.lrange(chave_eventos_lote, 0, -1) or []
    if not contagem and not eventos_brutos:
        _limpar(client, chave_contagem_lote, chave_eventos_lote)
        return resultado

    eventos = [e for e in (_parse_evento(b) for b in eventos_brutos) if e]

    try:
        if db.bind is not None and db.bind.dialect.name == "postgresql":
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(text("SET LOCAL statement_timeout = '30s'"))
        for link_id, n in contagem.items():
            db.execute(
                text(
                    "UPDATE custom_links "
                    "SET click_count = COALESCE(click_count, 0) + :n, updated_at = now() "
                    "WHERE id = :id"
                ),
                {"n": int(n), "id": int(link_id)},
            )
        resultado.links_atualizados = len(contagem)
        if eventos:
            db.execute(
                text(
                    "INSERT INTO custom_link_events (custom_link_id, user_id, created_at) "
                    "VALUES (:link_id, :user_id, :created_at)"
                ),
                [
                    {"link_id": l, "user_id": u, "created_at": c}
                    for (l, u, c) in eventos[:LOTE_MAX_EVENTOS]
                ],
            )
            resultado.eventos_gravados = min(len(eventos), LOTE_MAX_EVENTOS)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Descarga de cliques falhou; devolvendo lote ao buffer: %s", exc)
        _devolver(client, contagem, eventos_brutos)
        _limpar(client, chave_contagem_lote, chave_eventos_lote)
        raise

    # Eventos além do teto do lote voltam para o buffer principal (raro).
    sobra = eventos_brutos[LOTE_MAX_EVENTOS:]
    if sobra:
        try:
            client.rpush(CHAVE_EVENTOS, *sobra)
        except Exception as exc:
            logger.error("Não foi possível devolver %d eventos ao buffer: %s", len(sobra), exc)
    resultado.restantes = len(sobra)
    _limpar(client, chave_contagem_lote, chave_eventos_lote)
    return resultado


def _devolver(client, contagem: dict, eventos_brutos: list) -> None:
    try:
        pipe = client.pipeline(transaction=False)
        for link_id, n in contagem.items():
            pipe.hincrby(CHAVE_CONTAGEM, str(link_id), int(n))
        if eventos_brutos:
            pipe.rpush(CHAVE_EVENTOS, *eventos_brutos)
        pipe.execute()
    except Exception as exc:
        logger.critical("Lote de cliques PERDIDO ao devolver ao Redis: %s", exc)


def _limpar(client, *chaves: str) -> None:
    try:
        client.delete(*chaves)
    except Exception:
        pass


def tamanho_pendente() -> int:
    """Quantos eventos aguardam descarga (para /health e observabilidade)."""
    client = get_client()
    if client is None:
        return 0
    try:
        return int(client.llen(CHAVE_EVENTOS) or 0)
    except Exception:
        return 0
