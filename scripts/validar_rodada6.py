#!/usr/bin/env python3
"""Confere os números de aceite da rodada 6 contra o banco.

Uso:
  python scripts/validar_rodada6.py

Somente leitura. Sai com código 1 se algum aceite falhar — os aceites da rodada
anterior não foram executados e foi exatamente isso que deixou as cobranças
duplicadas passarem pro ar.
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

# nome (busca por prefixo, minúsculo) → total pago líquido esperado em centavos
TOTAIS_ESPERADOS = {
    "let": 18150,        # Letícia
    "alexandre": 18150,
    "bruna alves": 13570,
    "bruna cabral": 4235,
    "mariana": 9499,
}


def main() -> int:
    db = SessionLocal()
    falhas = []
    try:
        svc = AdminMetricsService(db)

        print("== Item 1 — totais pagos (cobrança única por order_ref) ==")
        clientes = svc.list_clients({})
        for busca, esperado in TOTAIS_ESPERADOS.items():
            achados = [c for c in clientes if busca in (c["name"] or "").lower()]
            if not achados:
                falhas.append(f"cliente '{busca}' não encontrado")
                print(f"  {busca:<14} NÃO ENCONTRADO")
                continue
            for c in achados:
                real = c["total_paid_net_cents"]
                ok = real == esperado
                if not ok:
                    falhas.append(f"{c['name']}: {real/100:.2f} != {esperado/100:.2f}")
                print(f"  {c['name'][:28]:<30} {real/100:>9.2f}  (esperado {esperado/100:.2f})  {'OK' if ok else 'FALHOU'}")

        print("\n== Itens 2/3/4/11 — dashboard de agosto/2026 ==")
        dash = svc.dashboard(2026, 8)
        checagens = [
            ("Ativos (renovando)", dash["active_count"], 30),
            ("MRR líquido (centavos)", dash["mrr_net_cents"], 141198),
            ("ARPU (centavos)", dash["arpu_cents"], 4707),
            ("Novas de agosto", dash["new_subscriptions"], 14),
            ("Churn de agosto", dash["churn_count"], 4),
        ]
        for rotulo, real, esperado in checagens:
            ok = real == esperado
            if not ok:
                falhas.append(f"{rotulo}: {real} != {esperado}")
            print(f"  {rotulo:<26} {real:>10}  (esperado {esperado})  {'OK' if ok else 'FALHOU'}")

        renovacao = dash["renewal_rate"]
        ok = renovacao is not None and abs(renovacao - 0.5) < 0.001
        if not ok:
            falhas.append(f"Taxa de renovação: {renovacao} != 0.5")
        print(f"  {'Taxa de renovação':<26} {renovacao!s:>10}  (esperado 0.5)  {'OK' if ok else 'FALHOU'}")

        ago = next(
            (p for p in dash["series"]["new_vs_canceled"] if p["month"] == "2026-08"), None
        )
        ok = ago == {"month": "2026-08", "novas": 14, "canceladas": 4}
        if not ok:
            falhas.append(f"Série novas x canceladas de agosto: {ago}")
        print(f"  {'Série ago (14 x 4)':<26} {ago!s:>10}  {'OK' if ok else 'FALHOU'}")

        print("\n== Item 2 — Daniel fora do MRR, com acesso ==")
        daniel = [c for c in clientes if "daniel" in (c["name"] or "").lower()]
        for c in daniel:
            print(f"  {c['name'][:28]:<30} status={c['status']} acesso_até={c['access_until']}")

        print("\n== Item 3 — Ana Ariel aparece uma vez, no Pro ==")
        ana = [c for c in clientes if "ana ariel" in (c["name"] or "").lower()]
        for c in ana:
            print(f"  {c['name'][:28]:<30} plano={c['plan']} status={c['status']}")
        if len(ana) != 1:
            falhas.append(f"Ana Ariel aparece {len(ana)} vezes (esperado 1)")

        print("\n== Faturamento por mês (bater com o export de vendas da Kiwify) ==")
        for mes in range(4, 9):
            rev = svc.revenue_for_month(2026, mes)
            print(f"  {mes:02d}/2026  líquido {rev['net']/100:>10.2f}  bruto {rev['gross']/100:>10.2f}")

        if falhas:
            print(f"\n{len(falhas)} ACEITE(S) FALHARAM:")
            for f in falhas:
                print(f"  - {f}")
            return 1
        print("\nTodos os aceites automáticos passaram.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
