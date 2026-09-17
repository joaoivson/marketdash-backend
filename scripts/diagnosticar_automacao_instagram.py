#!/usr/bin/env python3
"""Comentários REAIS de um post × o que o webhook entregou × o que o pipeline fez.

Leitura apenas: não envia direct, não grava nada.

POR QUE EXISTE (17/09/2026)
---------------------------
A automação 12 (@promosdabeatrizz_) tinha 50+ comentários no post e 4 directs.
O ledger (`instagram_webhook_entregas`) só mostra o que CHEGOU — não mostra o
que existe no post e nunca chegou, nem se chegou com OUTRO media_id (comentário
de anúncio vem com a mídia do anúncio e o post em `original_media_id`). Só a
Graph API diz quais comentários existem, e só casando o id de cada um com o
ledger dá para separar as três causas:

  chegou com o media_id do post  → o pipeline decidiu (ver desfecho)
  chegou com OUTRO media_id       → anúncio / cópia: é o caso do `original_media_id`
  não chegou                      → a Meta não entregou (ou foi antes do ledger)

USO (dentro do container da API, onde estão DATABASE_URL e a chave do token)
---------------------------------------------------------------------------
    python scripts/diagnosticar_automacao_instagram.py --automacao 12
    python scripts/diagnosticar_automacao_instagram.py --automacao 12 --linhas

Não imprime texto nem username dos comentários — só id, data, grupo e o rastro.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv

    ENV_FILE = os.environ.get("ENV_FILE", ".env")
    if (ROOT / ENV_FILE).exists():
        load_dotenv(ROOT / ENV_FILE, override=False)
except ImportError:  # no container as envs já vêm do ambiente
    pass

from app.db.session import SessionLocal  # noqa: E402
from app.models.instagram_automation import (  # noqa: E402
    InstagramAutomation,
    InstagramEvent,
    InstagramWebhookEntrega,
)
from app.repositories.instagram_automation_repository import (  # noqa: E402
    InstagramAutomationRepository,
)
from app.services.instagram_retroativo_service import InstagramRetroativoService  # noqa: E402

BRT = timezone(timedelta(hours=-3))
# Primeira linha do ledger em produção (migration 083). Comentário anterior a
# isto não tem como ter rastro de entrega — "não chegou" antes daqui não prova nada.
INICIO_DO_LEDGER = datetime(2026, 9, 11, 21, 21, tzinfo=timezone.utc)


def _brt(dt: datetime | None) -> str:
    return dt.astimezone(BRT).strftime("%d/%m %H:%M") if dt else "—"


async def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--automacao", type=int, required=True)
    p.add_argument("--linhas", action="store_true", help="imprime um comentário por linha")
    args = p.parse_args()

    db = SessionLocal()
    try:
        automacao = db.get(InstagramAutomation, args.automacao)
        if not automacao:
            print(f"Automação {args.automacao} não existe.")
            return 1

        servico = InstagramRetroativoService(InstagramAutomationRepository(db))
        _, _, classificados, truncado = await servico.levantar(automacao.user_id, automacao.id)
        ids = [c.comment_id for c in classificados]

        entregas = {
            e.item_id: e
            for e in db.query(InstagramWebhookEntrega)
            .filter(InstagramWebhookEntrega.item_id.in_(ids))
            .order_by(InstagramWebhookEntrega.recebido_em)
            .all()
        }
        eventos = {
            e.comment_id: e
            for e in db.query(InstagramEvent).filter(InstagramEvent.comment_id.in_(ids)).all()
        }

        print(
            f"Automação {automacao.id} · {automacao.nome} · status={automacao.status}\n"
            f"post {automacao.media_id} · {automacao.media_permalink}\n"
            f"criada em {_brt(automacao.created_at)} (BRT)\n"
        )

        rastro = Counter()
        midias_alheias = Counter()
        linhas = []
        for c in classificados:
            entrega = entregas.get(c.comment_id)
            evento = eventos.get(c.comment_id)
            if entrega is None:
                if c.timestamp and c.timestamp < INICIO_DO_LEDGER:
                    chave = "antes do ledger (sem como saber)"
                elif evento is not None:
                    chave = "sem linha no ledger, mas processado"
                else:
                    chave = "NÃO CHEGOU no webhook"
            elif entrega.media_id == automacao.media_id:
                chave = f"chegou com o id do post → {entrega.desfecho}"
            else:
                chave = f"chegou com OUTRO media_id → {entrega.desfecho}"
                midias_alheias[entrega.media_id] += 1
            rastro[chave] += 1
            linhas.append(
                f"  {c.comment_id:<20} {_brt(c.timestamp):<12} {c.grupo:<18} "
                f"{chave}"
                + (f" [{entrega.media_id}] {entrega.detalhe or ''}" if entrega else "")
                + (f" · evento={evento.dm_status}" if evento else "")
            )

        print(f"Comentários no post (com respostas): {len(classificados)}"
              + ("  ⚠️ TRUNCADO no teto de leitura" if truncado else ""))
        print("\nO que aconteceu com cada um no webhook:")
        for chave, n in rastro.most_common():
            print(f"  {n:>4}  {chave}")
        if midias_alheias:
            print("\nmedia_id com que chegaram (≠ do post):")
            for media_id, n in midias_alheias.most_common():
                print(f"  {n:>4}  {media_id}")

        grupos = Counter(c.grupo for c in classificados)
        print("\nEnvio retroativo HOJE (prévia):")
        for grupo, n in grupos.most_common():
            print(f"  {n:>4}  {grupo}")

        if args.linhas:
            print("\n  comment_id           quando(BRT)  grupo              rastro")
            print("\n".join(linhas))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
