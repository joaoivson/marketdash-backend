#!/usr/bin/env python3
"""Diagnóstico dos itens 1 e 4 da Rodada 7 — leitura apenas.

Item 1: new_subscriptions() já conta pela 1ª cobrança paga histórica do
assinante (não pelo status atual) — na leitura do código isso já é o
comportamento pedido pelo brief. Este script confere contra dado real se o
resultado bate com o esperado (8 em julho/2026) e, se não bater, procura
linhas com is_plan_change NULL (que a query exclui via `.is_(False)`).

Item 4: o fluxo principal de acesso (Supabase + AccessBeacon + dedupe de
2min) já parece protegido; o suspeito é o fallback legado de login
(app/api/v1/routes/auth.py) que grava UserLogin sem essa janela. Duas
linhas de UserLogin do mesmo usuário a menos de 2 minutos uma da outra só
podem existir se vieram por um caminho SEM dedupe — o fluxo principal não
consegue produzir isso. Este script conta, por usuário, quantas linhas
"gêmeas" (< 2min de distância) existem.

Uso: python scripts/diagnostico_rodada7.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from sqlalchemy import func

from app.db.session import SessionLocal
from app.models.subscription_event import SubscriptionEvent
from app.models.user_login import UserLogin
from app.services.admin_metrics_service import AdminMetricsService


def diagnostico_item_1(db) -> None:
    print("== Item 1 — Novas de julho/2026 ==")
    svc = AdminMetricsService(db)
    real = svc.new_subscriptions(2026, 7)
    print(f"  new_subscriptions(2026, 7) = {real}  (esperado pelo brief: 8)")
    if real == 8:
        print("  OK — código já conta certo, nada a corrigir aqui.")
        return
    nulos = (
        db.query(func.count(SubscriptionEvent.id))
        .filter(
            SubscriptionEvent.event_type.in_(["order_approved", "subscription_renewed", "compra_aprovada"]),
            SubscriptionEvent.received_at >= "2026-07-01",
            SubscriptionEvent.received_at <= "2026-07-31 23:59:59",
            SubscriptionEvent.is_plan_change.is_(None),
        )
        .scalar()
    )
    print(f"  Linhas pagas de julho com is_plan_change NULL: {nulos}")
    print("  Se > 0, é candidato à causa — investigar quais assinantes antes de mudar o filtro.")


def diagnostico_item_4(db) -> None:
    print("\n== Item 4 — Padrão de acesso duplicado (candidato: login legado sem dedupe) ==")
    logins = (
        db.query(UserLogin.user_id, UserLogin.logged_at)
        .order_by(UserLogin.user_id, UserLogin.logged_at)
        .all()
    )
    por_usuario: dict[int, list] = {}
    for uid, quando in logins:
        por_usuario.setdefault(uid, []).append(quando)

    achados = []
    for uid, datas in por_usuario.items():
        gemeos = 0
        for i in range(1, len(datas)):
            if (datas[i] - datas[i - 1]).total_seconds() < 120:
                gemeos += 1
        if gemeos:
            achados.append((uid, len(datas), gemeos))

    achados.sort(key=lambda t: t[2], reverse=True)
    print(f"  {'user_id':<10}{'total_acessos':<16}{'pares_<2min':<14}")
    for uid, total, gemeos in achados[:15]:
        print(f"  {uid:<10}{total:<16}{gemeos:<14}")
    if not achados:
        print("  Nenhum par de acessos < 2min encontrado — fluxo principal está se comportando.")
    else:
        print(f"\n  {len(achados)} usuário(s) com pelo menos 1 par < 2min — evidência de gravação")
        print("  sem dedupe (só o caminho legado de auth.py grava sem essa proteção).")


def main() -> int:
    db = SessionLocal()
    try:
        diagnostico_item_1(db)
        diagnostico_item_4(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
