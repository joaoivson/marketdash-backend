#!/usr/bin/env python3
"""Diagnóstico da Rodada 9 — explica os números antes de mexer na regra. Leitura apenas.

Uso:
    ENV_FILE=.env.backup-1208 python scripts/diagnostico_rodada9.py   # produção
    python scripts/diagnostico_rodada9.py                             # o .env atual

Três saídas:
  1. MRR assinante a assinante: o que o código soma HOJE (net e gross) × o que a
     regra pedida somaria (my_commission ÷ período; product_base_price ÷ período),
     lendo os campos reais do raw_payload — mostra exatamente quem infla e por quê;
  2. churn de agosto sob a regra atual × a nova ("cancelado pelo produtor" conta,
     exceto is_plan_change), com a lista nominal dos cancelamentos;
  3. série novas × canceladas — quais meses têm movimento (corte pro gráfico).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

ENV_FILE = os.environ.get("ENV_FILE", ".env")
load_dotenv(ROOT / ENV_FILE, override=True)

from app.core.ambiente import REF_HOMOLOGACAO, REF_PRODUCAO, identidade_do_banco  # noqa: E402
from app.core.plans import list_price_cents  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.models.subscription_event import SubscriptionEvent  # noqa: E402
from app.services.admin_metrics_service import (  # noqa: E402
    CANCEL_EVENTS,
    AdminMetricsService,
    _freq_divisor,
    _month_bounds,
    _normalize_plan_label,
)

BRL = lambda cents: f"R$ {cents/100:>9,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _qual_banco() -> str:
    ident = identidade_do_banco()
    if ident == REF_PRODUCAO:
        return f"PRODUÇÃO ({ident})"
    if ident == REF_HOMOLOGACAO:
        return f"homologação ({ident})"
    return f"desconhecido/local ({ident})"


def _payload_commissions(ev) -> dict:
    raw = ev.raw_payload or {}
    order = raw.get("order") or raw
    if not isinstance(order, dict):
        return {}
    c = order.get("Commissions") or order.get("commissions") or {}
    return c if isinstance(c, dict) else {}


def _payload_plan_frequency(ev) -> str | None:
    raw = ev.raw_payload or {}
    order = raw.get("order") or raw
    if not isinstance(order, dict):
        return None
    sub = order.get("Subscription") or order.get("subscription") or {}
    plan = sub.get("plan") if isinstance(sub, dict) else {}
    if not isinstance(plan, dict):
        return None
    return (plan.get("frequency") or plan.get("charge_frequency") or "").lower() or None


def _as_cents(v) -> int | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Kiwify manda inteiro em centavos nos campos de Commissions
    return int(round(f))


def mrr_assinante_a_assinante(svc: AdminMetricsService) -> None:
    print("\n" + "=" * 100)
    print("1. MRR ASSINANTE A ASSINANTE  (aceites base 25/08: líquido R$2.019,62 · bruto R$2.291,00)")
    print("=" * 100)
    actives = svc.renewing_subscribers()
    print(f"assinantes renovando: {len(actives)}  (base 25/08 era 42; vendas novas somam por cima)\n")
    hdr = (
        f"{'nome':<28} {'plano':<9} {'freq_ev':<10} {'freq_pago':<10} "
        f"{'net atual':>12} {'net regra':>12} {'gross atual':>12} {'gross regra':>12}  flags"
    )
    print(hdr)
    print("-" * len(hdr))
    tot = {"net_atual": 0.0, "net_regra": 0.0, "gross_atual": 0.0, "gross_regra": 0.0}
    for ev in sorted(actives, key=lambda e: (e.customer_name or "")):
        div_atual = _freq_divisor(ev.plan_frequency)
        paid = svc._last_paid_for(ev)
        n = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
        plano = _normalize_plan_label(ev.plan_name, ev.plan_id)
        tabela = list_price_cents(plano, ev.plan_frequency)
        g = tabela if tabela is not None else ((paid.amount_gross_cents if paid else ev.amount_gross_cents) or 0)

        # regra pedida: campos reais do webhook da última cobrança paga
        fonte = paid or ev
        comm = _payload_commissions(fonte)
        my_comm = _as_cents(comm.get("my_commission"))
        base_price = _as_cents(comm.get("product_base_price"))
        freq_pago = fonte.plan_frequency or _payload_plan_frequency(fonte)
        div_regra = _freq_divisor(freq_pago)
        net_regra = (my_comm if my_comm is not None else n) / div_regra
        gross_regra = (base_price if base_price is not None else (fonte.amount_gross_cents or g)) / div_regra

        net_atual = n / div_atual
        gross_atual = g / div_atual
        tot["net_atual"] += net_atual
        tot["net_regra"] += net_regra
        tot["gross_atual"] += gross_atual
        tot["gross_regra"] += gross_regra

        flags = []
        if not ev.plan_frequency:
            flags.append("freq_ev_VAZIA")
        if paid is not None and not paid.plan_frequency:
            flags.append("freq_pago_VAZIA")
        if my_comm is None:
            flags.append("sem_my_commission")
        if base_price is None:
            flags.append("sem_base_price")
        if abs(net_atual - net_regra) >= 1:
            flags.append(f"NET_DIFERE({(net_atual-net_regra)/100:+.2f})")
        if abs(gross_atual - gross_regra) >= 1:
            flags.append(f"GROSS_DIFERE({(gross_atual-gross_regra)/100:+.2f})")
        print(
            f"{(ev.customer_name or '?')[:27]:<28} {plano:<9} {str(ev.plan_frequency):<10} "
            f"{str(freq_pago):<10} {BRL(int(net_atual)):>12} {BRL(int(net_regra)):>12} "
            f"{BRL(int(gross_atual)):>12} {BRL(int(gross_regra)):>12}  {' '.join(flags)}"
        )
    print("-" * len(hdr))
    print(
        f"{'TOTAL':<60} {BRL(int(round(tot['net_atual']))):>12} {BRL(int(round(tot['net_regra']))):>12} "
        f"{BRL(int(round(tot['gross_atual']))):>12} {BRL(int(round(tot['gross_regra']))):>12}"
    )
    mrr = svc.mrr_cents(actives)
    print(f"\nmrr_cents() do código: net {BRL(mrr['net'])} · gross {BRL(mrr['gross'])}")


def churn_agosto(svc: AdminMetricsService, year: int, month: int) -> None:
    print("\n" + "=" * 100)
    print(f"2. CHURN {month:02d}/{year}  (aceite: 7 — Bruna entra, Ana Ariel e Luiz Fernando ficam fora)")
    print("=" * 100)
    start, end = _month_bounds(year, month)
    cancels = (
        svc.db.query(SubscriptionEvent)
        .filter(
            SubscriptionEvent.event_type.in_(CANCEL_EVENTS),
            SubscriptionEvent.received_at >= start,
            SubscriptionEvent.received_at <= end,
        )
        .order_by(SubscriptionEvent.received_at)
        .all()
    )
    print(f"{'nome':<32} {'quando (UTC)':<22} {'motivo':<28} plan_change  conta hoje?  conta na regra nova?")
    for c in cancels:
        motivo = (c.cancel_reason or "").strip()
        conta_hoje = not c.is_plan_change
        conta_nova = not c.is_plan_change
        print(
            f"{(c.customer_name or '?')[:31]:<32} {str(c.received_at)[:19]:<22} {motivo[:27]:<28} "
            f"{str(bool(c.is_plan_change)):<12} {str(conta_hoje):<12} {conta_nova}"
        )
    atual = svc.churn_for_month(year, month)
    print(f"\nchurn_for_month() atual: count={atual['count']} rate={atual['rate']} base={atual['start_actives']}")


def serie_novas_canceladas(svc: AdminMetricsService) -> None:
    print("\n" + "=" * 100)
    print("3. SÉRIE NOVAS × CANCELADAS  (aceite: só meses com movimento — abril/2026 em diante)")
    print("=" * 100)
    for ponto in svc.new_vs_canceled_series():
        marca = "" if (ponto["novas"] or ponto["canceladas"]) else "   <-- sem movimento"
        print(f"  {ponto['month']}: novas={ponto['novas']:>3} canceladas={ponto['canceladas']:>3}{marca}")


def main() -> int:
    print(f"env carregado : {ENV_FILE}")
    print(f"banco         : {_qual_banco()}")
    db = SessionLocal()
    try:
        svc = AdminMetricsService(db)
        mrr_assinante_a_assinante(svc)
        churn_agosto(svc, 2026, 8)
        serie_novas_canceladas(svc)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
