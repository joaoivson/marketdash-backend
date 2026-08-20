#!/usr/bin/env python3
"""Diagnóstico da Rodada 8 — explica os números antes de mexer na regra. Leitura apenas.

Uso:
    ENV_FILE=.env.backup-1208 python scripts/diagnostico_rodada8.py   # produção
    python scripts/diagnostico_rodada8.py                             # o .env atual

Três saídas:
  1. bruto do MRR assinante a assinante, marcando quem cai no FALLBACK (sem preço
     de tabela) — é o que explica a diferença contra a tabela do Luiz;
  2. retrato do início do mês (denominador do churn): o que o código conta HOJE
     × o que a regra pedida contaria, com a lista das diferenças;
  3. quantas cobranças pagas chegam sem `gross` — o ramo de `charges.py` que
     também usa `list_price_cents` e mexeria no FATURAMENTO.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
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
    AdminMetricsService,
    _freq_divisor,
    _month_bounds,
    _normalize_plan_label,
    _subscriber_key,
    build_coverage_periods,
    cancel_instants,
)
from app.services.charges import extract_paid_charges  # noqa: E402

BRL = lambda cents: f"R$ {cents/100:>9,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _qual_banco() -> str:
    ident = identidade_do_banco()
    if ident == REF_PRODUCAO:
        return f"PRODUÇÃO ({ident})"
    if ident == REF_HOMOLOGACAO:
        return f"homologação ({ident})"
    return f"desconhecido/local ({ident})"


def bruto_por_assinante(svc: AdminMetricsService) -> None:
    print("\n" + "=" * 78)
    print("1. BRUTO DO MRR, ASSINANTE A ASSINANTE   (aceite: R$ 1.841,50)")
    print("=" * 78)

    atuais = svc.renewing_subscribers()
    linhas = []
    total_frac = 0.0
    fallback_frac = 0.0
    for ev in atuais:
        plano = _normalize_plan_label(ev.plan_name, ev.plan_id)
        freq = ev.plan_frequency
        div = _freq_divisor(freq)
        tabela = list_price_cents(plano, freq)
        paid = svc._last_paid_for(ev)
        real = (paid.amount_gross_cents if paid else ev.amount_gross_cents) or 0
        usado = tabela if tabela is not None else real
        total_frac += usado / div
        if tabela is None:
            fallback_frac += usado / div
        linhas.append({
            "chave": _subscriber_key(ev),
            "email": (ev.customer_email or "")[:34],
            "plano": plano,
            "freq": freq or "-",
            "div": div,
            "tabela": tabela,
            "real": real,
            "usado": usado,
            "mensalizado": usado / div,
            "fallback": tabela is None,
        })

    linhas.sort(key=lambda x: (x["fallback"], x["plano"], x["freq"]), reverse=True)
    print(f"\n{'plano':10} {'freq':11} {'div':>3} {'tabela':>12} {'cobrança':>12} "
          f"{'usado/mês':>12}  origem   e-mail")
    print("-" * 100)
    for l in linhas:
        origem = "FALLBACK" if l["fallback"] else "tabela"
        tab = BRL(l["tabela"]) if l["tabela"] is not None else "        —"
        print(f"{l['plano']:10} {l['freq']:11} {l['div']:>3} {tab:>12} {BRL(l['real']):>12} "
              f"{BRL(int(round(l['mensalizado']))):>12}  {origem:8} {l['email']}")

    print("-" * 100)
    print(f"assinantes na base (renewing): {len(linhas)}")
    print(f"TOTAL BRUTO mensalizado      : {BRL(int(round(total_frac)))}")
    print(f"   dos quais por FALLBACK    : {BRL(int(round(fallback_frac)))} "
          f"({sum(1 for l in linhas if l['fallback'])} assinantes)")

    print("\n-- composição por plano/frequência (confronto com a tabela do Luiz) --")
    grupos: dict = {}
    for l in linhas:
        k = (l["plano"], l["freq"])
        g = grupos.setdefault(k, {"qtd": 0, "soma": 0.0})
        g["qtd"] += 1
        g["soma"] += l["mensalizado"]
    for (plano, freq), g in sorted(grupos.items()):
        print(f"  {plano:10} {freq:11} qtd={g['qtd']:>3}  subtotal={BRL(int(round(g['soma'])))}")


def retrato_inicio_do_mes(svc: AdminMetricsService, ano: int, mes: int) -> None:
    print("\n" + "=" * 78)
    print(f"2. DENOMINADOR DO CHURN de {mes:02d}/{ano}   (aceite: 6 ÷ 20 = 30,0%)")
    print("=" * 78)

    start, _ = _month_bounds(ano, mes)
    instante = start - timedelta(seconds=1)  # 31/07 23:59:59

    # como está HOJE
    hoje = svc.renewing_subscribers(as_of=instante.date())
    chaves_hoje = {_subscriber_key(e) for e in hoje}

    # como a regra pedida contaria: vigência paga cobrindo o instante, sem
    # cancelamento até lá (mesma mecânica de mrr_at)
    eventos = svc._all_events()
    periodos = build_coverage_periods(eventos)
    cancelamentos = cancel_instants(eventos)
    chaves_regra = set()
    for chave, lista in periodos.items():
        cobrindo = None
        for p in lista:
            if p["inicio"] <= instante < p["fim"]:
                if cobrindo is None or p["inicio"] > cobrindo["inicio"]:
                    cobrindo = p
        if not cobrindo:
            continue
        if any(cobrindo["inicio"] <= c <= instante for c in cancelamentos.get(chave, [])):
            continue
        chaves_regra.add(chave)

    churn = svc.churn_for_month(ano, mes)
    print(f"\ninstante do retrato          : {instante.isoformat()}")
    print(f"denominador HOJE (código)    : {len(chaves_hoje)}")
    print(f"denominador pela REGRA nova  : {len(chaves_regra)}")
    print(f"cancelamentos contados no mês: {churn['count']}")
    if len(chaves_regra):
        print(f"churn pela regra nova        : {churn['count']}/{len(chaves_regra)} = "
              f"{churn['count']/len(chaves_regra)*100:.1f}%")
    print(f"churn como o painel mostra   : {churn['count']}/{churn['start_actives']} = "
          f"{churn['rate']*100:.1f}%")

    so_hoje = chaves_hoje - chaves_regra
    so_regra = chaves_regra - chaves_hoje
    email_por_chave = {}
    for ev in eventos:
        email_por_chave.setdefault(_subscriber_key(ev), ev.customer_email or "?")

    print(f"\n-- {len(so_hoje)} contados HOJE que a regra nova EXCLUI "
          f"(entraram depois do corte / sem vigência paga cobrindo o instante) --")
    for c in sorted(so_hoje):
        print(f"   {email_por_chave.get(c,'?')[:44]:44} {c}")
    print(f"\n-- {len(so_regra)} que a regra nova INCLUI e hoje ficam de fora "
          f"(estavam vivos em {instante.date()}, cancelaram depois) --")
    for c in sorted(so_regra):
        print(f"   {email_por_chave.get(c,'?')[:44]:44} {c}")


def cobrancas_sem_bruto(db) -> None:
    print("\n" + "=" * 78)
    print("3. COBRANÇAS SEM `gross` — o ramo de charges.py que mexe no FATURAMENTO")
    print("=" * 78)

    eventos = db.query(SubscriptionEvent).all()
    sem_gross = [e for e in eventos if not (getattr(e, "amount_gross_cents", None) or 0)]
    por_plano: dict = {}
    for e in sem_gross:
        plano = _normalize_plan_label(e.plan_name, e.plan_id)
        por_plano[plano] = por_plano.get(plano, 0) + 1

    print(f"\neventos totais                    : {len(eventos)}")
    print(f"eventos sem amount_gross_cents    : {len(sem_gross)}")
    for plano, qtd in sorted(por_plano.items()):
        marca = "  <-- muda com o preço novo do Max" if plano == "max" else ""
        print(f"   {plano:10} {qtd:>4}{marca}")
    if por_plano.get("max", 0) == 0:
        print("\n=> Nenhuma cobrança Max sem bruto: corrigir o preço do Max NÃO altera o faturamento.")
    else:
        print("\n=> ATENÇÃO: há cobrança Max sem bruto — o faturamento muda. Conferir com a Kiwify.")

    # quantas cobranças pagas de fato passam pelo fallback do extract_paid_charges
    cobrancas = extract_paid_charges(eventos)
    print(f"cobranças pagas extraídas         : {len(cobrancas)}")


def main() -> int:
    print(f"env carregado : {ENV_FILE}")
    print(f"banco         : {_qual_banco()}")
    db = SessionLocal()
    try:
        svc = AdminMetricsService(db)
        bruto_por_assinante(svc)
        hoje = datetime.now(timezone.utc).date()
        retrato_inicio_do_mes(svc, hoje.year, hoje.month)
        cobrancas_sem_bruto(db)
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
