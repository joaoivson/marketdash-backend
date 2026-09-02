#!/usr/bin/env python3
"""Desfaz pareamentos de upgrade FALSOS já gravados (Rodada 9, item 2).

O pareamento antigo comparava `plan_name` cru — "Pro" (import) vs "PRO - Mensal"
(webhook) parecia "plano diferente" e casava como upgrade. Um cancelamento real
21 dias depois da assinatura ficava com `is_plan_change=True` e sumia do churn
(caso Bruna Cabral, 24/08). A regra foi corrigida no recorder para comparar o
plano NORMALIZADO; este script reavalia os eventos de WEBHOOK já marcados e
desmarca quem não tem par sob a regra corrigida.

Escopo deliberado: só eventos de webhook (dedupe_key sem prefixo "import:").
Os flags do import histórico vieram de decisão própria do import e ficam como
estão.

Uso:
    ENV_FILE=.env.backup-1208 python scripts/backfill_rodada9_plan_changes.py --dry-run
    ENV_FILE=.env.backup-1208 python scripts/backfill_rodada9_plan_changes.py

Idempotente: só escreve em quem está com is_plan_change=True e perdeu o par.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

ENV_FILE = os.environ.get("ENV_FILE", ".env")
load_dotenv(ROOT / ENV_FILE, override=True)

from app.core.ambiente import identidade_do_banco  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.subscription_event import SubscriptionEvent  # noqa: E402
from app.services.subscription_event_recorder import (  # noqa: E402
    PAID_LIKE_EVENTS,
    encontrar_par_de_plan_change,
)

TIPOS = list(PAID_LIKE_EVENTS) + ["subscription_canceled"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print(f"env carregado : {ENV_FILE}")
    print(f"banco         : {identidade_do_banco()}")

    db = SessionLocal()
    try:
        eventos = (
            db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.is_plan_change.is_(True),
                SubscriptionEvent.event_type.in_(TIPOS),
                ~SubscriptionEvent.dedupe_key.like("import:%"),
            )
            .order_by(SubscriptionEvent.received_at.asc(), SubscriptionEvent.id.asc())
            .all()
        )
        print(f"eventos de webhook marcados como plan_change: {len(eventos)}\n")
        desmarcados = 0
        for ev in eventos:
            fields = {
                "event_type": (ev.event_type or "").lower(),
                "customer_cpf": ev.customer_cpf,
                "plan_name": ev.plan_name,
                "plan_id": ev.plan_id,
                "plan_frequency": ev.plan_frequency,
            }
            ref = ev.received_at
            if ref is not None and ref.tzinfo is None:
                ref = ref.replace(tzinfo=timezone.utc)
            par = encontrar_par_de_plan_change(db, fields, reference_time=ref)
            veredicto = "mantém (par real)" if par is not None else "DESMARCA (sem par na regra nova)"
            print(
                f"  {str(ev.received_at)[:19]}  {ev.event_type:<24} "
                f"{(ev.customer_name or '?')[:28]:<30} plano={ev.plan_name!r:<22} -> {veredicto}"
            )
            if par is None:
                desmarcados += 1
                if not args.dry_run:
                    ev.is_plan_change = False

        if args.dry_run:
            print(f"\n[dry-run] desmarcaria {desmarcados} evento(s). Nada foi escrito.")
        else:
            db.commit()
            print(f"\ndesmarcados {desmarcados} evento(s). Commit feito.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
