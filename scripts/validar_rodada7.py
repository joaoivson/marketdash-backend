#!/usr/bin/env python3
"""Confere os 11 aceites da Rodada 7 contra o banco. Leitura apenas.

Uso: python scripts/validar_rodada7.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app.db.session import SessionLocal
from app.services.admin_metrics_service import AdminMetricsService
from app.services.platform_usage_service import PlatformUsageService


def main() -> int:
    db = SessionLocal()
    falhas = []
    try:
        svc = AdminMetricsService(db)
        uso = PlatformUsageService(db)

        print("== 1. Novas x Canceladas — julho 8x6, agosto 16x4 ==")
        dash = svc.dashboard(2026, 8)
        julho = next((p for p in dash["series"]["new_vs_canceled"] if p["month"] == "2026-07"), None)
        agosto = next((p for p in dash["series"]["new_vs_canceled"] if p["month"] == "2026-08"), None)
        print(f"  julho: {julho}  (esperado novas=8, canceladas=6)")
        print(f"  agosto: {agosto}  (esperado novas=16, canceladas=4 — base 12/08)")
        if not julho or julho["novas"] != 8:
            falhas.append(f"Novas de julho: {julho}")

        print("\n== 2. Churn de agosto ~20% (denominador = renovando em 01/08) ==")
        churn = svc.churn_for_month(2026, 8)
        print(f"  {churn}  (esperado rate ~0.20, start_actives ~20)")
        if not (0.15 <= churn["rate"] <= 0.25):
            falhas.append(f"Churn rate fora da faixa: {churn['rate']}")

        print("\n== 3. Bruto do MRR = R$1.766,50 (base 12/08) ==")
        print(f"  mrr_gross_cents = {dash['mrr_gross_cents']}  (esperado 176650)")
        if dash["mrr_gross_cents"] != 176650:
            falhas.append(f"MRR bruto: {dash['mrr_gross_cents']} != 176650")

        print("\n== 5. Janela 7d — Dias ativos <= 7 pra todo mundo ==")
        atividade = uso.atividade_por_usuaria("7d")
        estourou = [a for a in atividade if a["dias_ativos"] > 7]
        for a in estourou:
            print(f"  ESTOUROU: {a['nome']} — {a['dias_ativos']} dias")
            falhas.append(f"{a['nome']}: dias_ativos={a['dias_ativos']} > 7")
        if not estourou:
            print("  OK — nenhuma usuária com mais de 7 dias ativos na janela de 7d.")

        print("\n== 9. Essencial sem Links/Páginas mostra plano corretamente ==")
        essenciais = [a for a in atividade if a.get("plan") == "essencial"]
        print(f"  {len(essenciais)} usuária(s) Essencial na janela — plano populado: {all('plan' in a for a in atividade)}")

        print("\n== 10. Card sem_acesso_10d bate com list_clients(no_login_10d=True) ==")
        cards = uso.cards("7d")
        lista_filtrada = svc.list_clients({"status": "ativo,atrasado,cancelado_com_acesso", "no_login_10d": True})
        print(f"  card = {cards['sem_acesso_10d']}  ·  lista filtrada = {len(lista_filtrada)}")
        if cards["sem_acesso_10d"] != len(lista_filtrada):
            falhas.append(
                f"Card sem_acesso_10d ({cards['sem_acesso_10d']}) != lista filtrada ({len(lista_filtrada)})"
            )

        print(f"\n{'='*60}")
        if falhas:
            print(f"FALHOU — {len(falhas)} aceite(s) não bateram:")
            for f in falhas:
                print(f"  - {f}")
            return 1
        print("Todos os aceites automatizáveis bateram.")
        print("Aceites 4 (teste da 1h), 6/7/8 (visual), 11 (regressão) são manuais — ver relatório da Task 15.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
