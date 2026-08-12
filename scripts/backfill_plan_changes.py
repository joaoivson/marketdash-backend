#!/usr/bin/env python3
"""Re-aplica a regra de upgrade/continuação bidirecional (rodada 6, item 3).

Os eventos da Ana Ariel (e de qualquer par em que a nova assinatura veio ANTES
do cancelamento) já estão no banco sem `is_plan_change`. Este script varre o
histórico com a regra nova e marca os pares que faltam.

Uso:
  python scripts/backfill_plan_changes.py --dry-run   # só lista o que marcaria
  python scripts/backfill_plan_changes.py             # marca de verdade

Idempotente: só escreve em quem está com is_plan_change=False.
Rodar UMA VEZ depois do deploy da task 5.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.db.session import SessionLocal
from app.models.subscription_event import SubscriptionEvent
from app.services.subscription_event_recorder import encontrar_par_de_plan_change

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("backfill_plan_changes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        eventos = (
            db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.customer_cpf.isnot(None),
                SubscriptionEvent.is_plan_change.is_(False),
            )
            .order_by(SubscriptionEvent.received_at.asc(), SubscriptionEvent.id.asc())
            .all()
        )
        logger.info("Candidatos: %d eventos sem is_plan_change", len(eventos))

        marcados = 0
        for ev in eventos:
            if ev.is_plan_change:
                continue  # já marcado por um par processado nesta mesma varredura
            fields = {
                "event_type": ev.event_type,
                "customer_cpf": ev.customer_cpf,
                "plan_name": ev.plan_name,
                "plan_frequency": ev.plan_frequency,
            }
            par = encontrar_par_de_plan_change(db, fields, reference_time=ev.received_at)
            if par is None or par.id == ev.id:
                continue
            logger.info(
                "Par: #%s %s (%s) <-> #%s %s (%s) — cpf %s",
                ev.id, ev.event_type, ev.plan_name,
                par.id, par.event_type, par.plan_name,
                ev.customer_cpf,
            )
            ev.is_plan_change = True
            par.is_plan_change = True
            marcados += 2

        logger.info("Eventos marcados: %d", marcados)
        if args.dry_run:
            db.rollback()
            logger.info("--dry-run: rollback (nada salvo).")
        else:
            db.commit()
            logger.info("Commit realizado.")
        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
