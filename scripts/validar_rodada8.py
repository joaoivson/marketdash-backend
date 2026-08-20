#!/usr/bin/env python3
"""Confere os aceites da Rodada 8 contra o banco. Leitura apenas.

Uso:
    ENV_FILE=.env.backup-1208 python scripts/validar_rodada8.py   # produção
    python scripts/validar_rodada8.py                             # o .env atual
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
from app.services.admin_metrics_service import (  # noqa: E402
    AdminMetricsService,
    CANCEL_EVENTS,
    PRODUTOR_ADJUSTMENT_REASONS,
    _month_bounds,
    _subscriber_key,
    assinaturas_atrasadas_em,
    assinaturas_pagas_em,
    build_coverage_periods,
    cancel_instants,
)
from app.models.subscription_event import SubscriptionEvent  # noqa: E402


def brl(cents: int) -> str:
    return f"R$ {cents/100:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def banco() -> str:
    ident = identidade_do_banco()
    return {REF_PRODUCAO: "PRODUÇÃO", REF_HOMOLOGACAO: "homologação"}.get(
        ident, "local/desconhecido"
    ) + f" ({ident})"


def main() -> int:
    print(f"env  : {ENV_FILE}")
    print(f"banco: {banco()}\n")
    db = SessionLocal()
    falhas: list[str] = []
    try:
        svc = AdminMetricsService(db)
        hoje = datetime.now(timezone.utc).date()

        # ------------------------------------------------------------ 1 --
        print("== 1. Denominador do churn = base do início do mês ==")
        churn = svc.churn_for_month(hoje.year, hoje.month)
        print(f"   base do início do mês : {churn['start_actives']}")
        print(f"   cancelamentos no mês  : {churn['count']}")
        print(f"   churn                 : {churn['rate']*100:.1f}%")

        # invariante: numerador ⊆ denominador, exceto quem nasceu no mês
        start, end = _month_bounds(hoje.year, hoje.month)
        instante = start - timedelta(seconds=1)
        eventos = svc._all_events()
        periodos = build_coverage_periods(eventos)
        cancelamentos = cancel_instants(eventos)
        base = set(assinaturas_pagas_em(instante, periodos, cancelamentos))
        base |= assinaturas_atrasadas_em(instante, eventos, cancelamentos)

        cancels = [
            c for c in db.query(SubscriptionEvent).filter(
                SubscriptionEvent.event_type.in_(CANCEL_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.is_(False),
            ).all()
            if (c.cancel_reason or "").strip().lower() not in PRODUTOR_ADJUSTMENT_REASONS
        ]
        email = {}
        for ev in eventos:
            email.setdefault(_subscriber_key(ev), ev.customer_email or "?")
        from app.services.admin_metrics_service import _identidade_da_pessoa, _pessoa_por_chave
        pessoa = _pessoa_por_chave(eventos)
        base = {pessoa.get(k, k) for k in base}
        fora = {pessoa.get(_subscriber_key(c), _identidade_da_pessoa(c)) for c in cancels} - base
        if fora:
            print(f"   ⚠ {len(fora)} no numerador que NÃO estão na base "
                  f"(entraram e saíram dentro do mês):")
            for k in sorted(fora):
                print(f"       {k}")

        print("   -- quem forma a base (confira nominalmente na Kiwify) --")
        for k in sorted(base, key=lambda x: email.get(x, "")):
            print(f"       {k}")

        # ------------------------------------------------------------ 2 --
        print("\n== 2. Bruto do MRR pelo preço de tabela ==")
        mrr = svc.mrr_cents()
        atuais = svc.renewing_subscribers()
        print(f"   assinantes (renovando): {len(atuais)}")
        print(f"   bruto                 : {brl(mrr['gross'])}")
        print(f"   líquido               : {brl(mrr['net'])}")
        sem_tabela = [
            ev for ev in atuais
            if list_price_cents(
                (ev.plan_name or ev.plan_id or ""), ev.plan_frequency
            ) is None
        ]
        if sem_tabela:
            falhas.append(f"{len(sem_tabela)} assinante(s) sem preço de tabela (fallback)")
            for ev in sem_tabela:
                print(f"   ⚠ sem tabela: {ev.customer_email} {ev.plan_name}/{ev.plan_frequency}")
        else:
            print("   ✓ nenhum assinante caindo no fallback da cobrança real")

        if list_price_cents("max", "mensal") != 9700:
            falhas.append("Max mensal não está R$97 no catálogo")
        else:
            print("   ✓ Max mensal = R$ 97,00 (era precificado como Pro)")

        # ------------------------------------------------------------ 4 --
        print("\n== 4. Ponto do mês corrente do gráfico == card de MRR ==")
        series = svc.series_12m()
        ultimo = series["mrr"][-1] if series["mrr"] else None
        if not ultimo:
            falhas.append("série de MRR vazia")
        else:
            print(f"   último ponto ({ultimo['month']}): "
                  f"líq {brl(ultimo['net'])} / bruto {brl(ultimo['gross'])}")
            print(f"   card                     : "
                  f"líq {brl(mrr['net'])} / bruto {brl(mrr['gross'])}")
            if (ultimo["net"], ultimo["gross"]) != (mrr["net"], mrr["gross"]):
                falhas.append("último ponto da série ≠ card de MRR")
            else:
                print("   ✓ batem")

        # ------------------------------------------------------------ 5 --
        print("\n== 5. Nada quebrado: contadores gerais ==")
        print(f"   ativos (com acesso)   : {len(svc.active_subscribers())}")
        print(f"   renovando             : {len(atuais)}")
        print(f"   pontos na série       : {len(series['mrr'])} (MRR) / "
              f"{len(series['revenue'])} (receita)")
    finally:
        db.close()

    print("\n" + ("FALHAS: " + "; ".join(falhas) if falhas else "Sem falhas automáticas."))
    return 1 if falhas else 0


if __name__ == "__main__":
    raise SystemExit(main())
