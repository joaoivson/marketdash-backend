# Painel Admin — Rodada 7 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar os 10 itens de acabamento da Rodada 7 do painel admin (brief de Luiz Fernando, 12/08/2026): 7 fixes diretos já localizados no código, 1 fix de definição de negócio (bruto do MRR = preço de tabela), e 2 itens que exigem diagnóstico com dado real antes de decidir se há algo pra corrigir.

**Architecture:** Backend deriva tudo de `subscription_events`/`user_logins` em tempo de query (sem tabelas de cobrança nem cache pré-calculado) — mesma arquitetura da Rodada 6. Os fixes desta rodada são cirúrgicos: um denominador trocado (`churn_for_month`), uma fonte de valor trocada (`mrr_cents` passa a usar catálogo de preço, não a última cobrança), uma janela de tempo realinhada a dia civil BRT (`_inicio`/`atividade_por_usuaria`/`usuarias_por_dia`, migrando de `cast(..., Date)` em SQL — que trunca no fuso da sessão do Postgres, não BRT — para bucketing em Python com o helper `_brt_date` já existente), e um parâmetro de query desalinhado entre card e lista (`sem_acesso` → `no_login_10d`). Dois itens (novas de julho, registro de acesso duplicado) têm código que já parece correto na leitura — a Task 1 roda um diagnóstico contra dado real antes de qualquer um deles virar uma "correção" de algo que pode não estar quebrado.

**Tech Stack:** FastAPI + SQLAlchemy + PostgreSQL (Supabase) no backend; React + Vite + Recharts + shadcn/ui no frontend; pytest.

## Global Constraints

- Comandos de teste do backend: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest <caminho> -v` — `pytest tests/ -v` sozinho **não funciona** (venv 3.9 quebra na coleção).
- Frontend: `cd marketdash-frontend && npx tsc --noEmit && npm run lint` antes de cada commit que toca `.tsx`/`.ts`.
- Camadas do backend: `routes → services → repositories → models`. Não pular camadas.
- Não modificar componentes em `marketdash-frontend/src/components/ui/` — estender via wrapper.
- **Diagnóstico contra produção (Task 1 e Task 14) é leitura apenas.** Nenhum `UPDATE`/`INSERT`/`DELETE`, nenhuma migration.
- Nenhuma migration de banco é necessária em nenhuma task deste plano.
- **Definição de negócio confirmada com o usuário (item 3):** "bruto do MRR" = preço de TABELA vigente do plano/periodicidade (`app/core/plans.py::list_price_cents`), não o valor real da última cobrança paga. "Líquido" continua vindo da última cobrança paga — não muda.
- Commits seguem `tipo(escopo): descrição curta` (ex.: `fix(admin): denominador do churn usa renewing_subscribers`).
- Base de referência para os números de aceite: dado real de produção em 12/08/2026 (13 Pro Mensal + 6 Essencial Mensal + 11 Pro Trimestral + 2 Pro Anual → bruto esperado R$1.766,50; churn de agosto ≈ 20%).

---

## Contexto que o implementador precisa saber

**Por que "bruto" muda de fonte (item 3).** `AdminMetricsService.mrr_cents()` (`app/services/admin_metrics_service.py:390`) hoje usa `paid.amount_gross_cents` — o valor real da última cobrança paga, que pode vir com desconto/cupom histórico. O catálogo de preço de tabela já existe em `app/core/plans.py::PLAN_LIST_PRICE_CENTS`/`list_price_cents(plan, frequency)`. A função de normalização de rótulo de plano (`plan_name`/`plan_id` → `"essencial"`/`"pro"`/`"max"`) já existe DUPLICADA em `admin_metrics_service.py:154` (`_normalize_plan_label`, já usada em 4 outros pontos do arquivo) — não precisa reimportar de `charges.py`, é só reusar a que já está no mesmo arquivo.

**Por que a janela de 7d mostra 8 dias (item 5).** `PlatformUsageService._inicio()` (`app/services/platform_usage_service.py:103`) calcula `agora - timedelta(days=7)` em UTC — um instante, não um conjunto de dias civis. `atividade_por_usuaria()` (linha 270) e `usuarias_por_dia()` (linha 180) agrupam por `cast(UserLogin.logged_at, Date)`, que no Postgres trunca pelo fuso da SESSÃO (não BRT) — uma janela de 7 dias que começa no meio da tarde cobre pedaços de até 8 datas. O arquivo `admin_metrics_service.py` já resolve exatamente esse problema em outro contexto com `BRT = ZoneInfo("America/Sao_Paulo")` e `_brt_date(dt) -> date` (linha 46 e 63) — uma função pura em Python aplicada a datetimes já buscados do banco, não um cast SQL. Esta rodada estende o mesmo padrão pra `platform_usage_service.py`, trocando o `cast(...,Date)`/`group_by` em SQL por bucketing em Python com `_brt_date`.

**Por que o import é sempre local (deferred), nunca no topo do arquivo.** `platform_usage_service.py` e `admin_metrics_service.py` se importam mutuamente hoje (`_base_ativa()` linha 174 e `_uso_links_paginas_30d()` linha 348), sempre com `import` dentro do método, nunca no topo do arquivo — convenção já estabelecida no código pra evitar import circular. As tasks que reusam `_brt_date`/`_normalize_plan_label`/`AdminMetricsService` de dentro de `platform_usage_service.py` seguem o mesmo padrão.

**Achado confirmado durante o levantamento (item 4).** `POST /api/v1/auth/login` (`app/api/v1/routes/auth.py:29-56`, fallback legado pré-Supabase) grava `UserLogin` direto (`db.add(UserLogin(...))`, linha 49) **sem** a janela de dedupe de 2 minutos que `daily_access_service.record_access()` já implementa para o fluxo principal (Supabase + `AccessBeacon`, que só dispara em `SIGNED_IN`). Isso não é hipótese — é o código lido diretamente. A Task 7 corrige isso independentemente do resultado do diagnóstico da Task 1, que serve pra QUANTIFICAR o impacto histórico (quantos dos 126 acessos de Daniele vieram daqui), não pra decidir SE existe o bug.

## File Structure

**Backend — criar**
- `scripts/diagnostico_rodada7.py` — leitura apenas; checa item 1 (novas de julho) e mede o padrão de acesso duplicado do item 4, antes de qualquer fix nesses dois itens.
- `scripts/validar_rodada7.py` — versão final, cobre os 11 aceites do brief (Task 14, expande o diagnóstico da Task 1).
- `tests/unit/test_churn_denominador_renovando.py`
- `tests/unit/test_mrr_bruto_preco_de_tabela.py`
- `tests/unit/test_platform_usage_janela_brt.py`
- `tests/unit/test_atividade_usuaria_plano.py`
- `tests/unit/test_export_csv_com_filtros.py`
- `tests/unit/test_login_legado_dedupe.py`

**Backend — modificar**
- `app/services/admin_metrics_service.py` — `churn_for_month` (denominador), `mrr_cents` (fonte do bruto).
- `app/services/platform_usage_service.py` — `_inicio`, `usuarias_por_dia`, `atividade_por_usuaria` (janela BRT + campo `plan`).
- `app/api/v1/routes/admin_panel.py` — `export_clients_csv` aceita os mesmos filtros de `GET /clients`.
- `app/api/v1/routes/auth.py` — `/login` legado usa `record_access` em vez de insert direto.

**Frontend — criar**
- Nada novo — reusa `AdminTableFooter.tsx` (paginação) e `chart-defaults.tsx` (margin dos gráficos).

**Frontend — modificar**
- `src/features/admin/components/chart-defaults.tsx` — `CHART_MARGIN` (item 6).
- `src/features/admin/pages/AdminDashboard.tsx` — margin nos gráficos MRR/Faturamento; grid 2×2 (itens 6, 7).
- `src/features/admin/components/AdminTableFooter.tsx` — formato "Mostrando X–Y de Z" (item 8).
- `src/features/admin/pages/AdminClients.tsx` — paginação; CSV com filtros; chip de alerta nomeado (itens 8, 10).
- `src/features/admin/components/PlatformUsageTab.tsx` — "—" pra Essencial; link do card corrigido (itens 9, 10).
- `src/features/admin/pages/AdminClientDetail.tsx` — "—" pra Essencial (item 9).
- `src/services/admin-panel.service.ts` — campo `plan` em `PlatformUsage.atividade` (item 9).

---

## Fase 1 — Diagnóstico antes de decidir (Grupo B)

### Task 1: Diagnóstico dos itens 1 e 4 contra dado real

**Files:**
- Create: `scripts/diagnostico_rodada7.py`

**Interfaces:**
- Consumes: `AdminMetricsService(db).new_subscriptions(year, month)` (já existe, `app/services/admin_metrics_service.py:478`); `UserLogin` model (`app/models/user_login.py`).
- Produces: saída impressa (não é um teste automatizado — é diagnóstico manual, sem assert que quebra CI).

- [ ] **Step 1: Escrever o script**

```python
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
```

- [ ] **Step 2: Rodar contra o banco configurado em `.env`**

Run: `PYTHONPATH=$PWD .venv312/bin/python scripts/diagnostico_rodada7.py`

Isso roda contra o banco do `.env` local (hml, pelo padrão do projeto). Para
checar contra PRODUÇÃO como as rodadas anteriores fizeram, trocar
temporariamente a `DATABASE_URL` usada pelo `load_dotenv` por uma apontando
pro pooler de produção (mesmo procedimento manual da Rodada 6, Task 14 —
não commitar credencial nenhuma).

- [ ] **Step 3: Documentar o resultado no relatório da task**

Anexar a saída completa do script (os dois blocos) ao relatório desta
task — Task 13 (item 1) e Task 7 (item 4, só para quantificar o impacto
histórico) leem esse resultado.

- [ ] **Step 4: Commit**

```bash
git add scripts/diagnostico_rodada7.py
git commit -m "chore(admin): script de diagnóstico dos itens 1 e 4 da rodada 7"
```

---

## Fase 2 — Fixes confirmados (Grupo A)

### Task 2: Item 2 — churn usa `renewing_subscribers()` como denominador

**Files:**
- Modify: `app/services/admin_metrics_service.py:509-532` (`churn_for_month`)
- Test: `tests/unit/test_churn_denominador_renovando.py`

**Interfaces:**
- Consumes: `AdminMetricsService.renewing_subscribers(as_of: Optional[date]) -> List[SubscriptionEvent]` (já existe, linha 380).
- Produces: `churn_for_month(year, month) -> {"count": int, "rate": float, "start_actives": int}` — assinatura não muda, só o cálculo de `start_actives`/`rate`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 2: denominador do churn passa a ser quem estava
RENOVANDO no início do mês, não toda a base com acesso (que inclui
cancelado-com-acesso — gente que não é mais receita recorrente esperada,
mas segue "ativa" pro produto)."""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        subscription_id="sub-1",
        customer_email="a@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=None,
        next_payment=None,
        amount_net_cents=None,
        amount_gross_cents=None,
        fee_cents=None,
        approved_date=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_churn_denominador_usa_renovando_nao_toda_a_base_de_acesso():
    svc = AdminMetricsService(MagicMock())

    renovando = [_ev(id=i, subscription_id=f"sub-{i}") for i in range(1, 21)]  # 20 renovando
    cancelada_com_acesso = [
        _ev(
            id=100 + i,
            subscription_id=f"sub-canc-{i}",
            event_type="subscription_canceled",
            subscription_status="canceled",
        )
        for i in range(17)
    ]  # +17 com acesso mas já cancelada = 37 na base de acesso total

    svc.active_subscribers = lambda as_of=None: renovando + cancelada_com_acesso
    svc.renewing_subscribers = lambda as_of=None: renovando

    svc.db.query.return_value.filter.return_value.all.return_value = []  # nenhum cancelamento no mês
    svc._agora = lambda: datetime(2026, 8, 31, 23, 59, tzinfo=timezone.utc)

    resultado = svc.churn_for_month(2026, 8)

    assert resultado["start_actives"] == 20  # não 37
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_churn_denominador_renovando.py -v`
Expected: FAIL — `start_actives == 37`, não 20 (código atual usa `active_subscribers`).

- [ ] **Step 3: Aplicar o fix**

Em `app/services/admin_metrics_service.py`, dentro de `churn_for_month` (linha 509-513), trocar:

```python
        start, end = _month_bounds(year, month)
        # ativos no início do mês
        start_actives = self.active_subscribers(as_of=(start - timedelta(seconds=1)).date())
        start_count = max(len(start_actives), 1)
```

por:

```python
        start, end = _month_bounds(year, month)
        # renovando no início do mês — cancelado-com-acesso segue "ativo" pro
        # produto (aba Uso), mas não é mais receita recorrente esperada, então
        # não conta no denominador do churn.
        start_actives = self.renewing_subscribers(as_of=(start - timedelta(seconds=1)).date())
        start_count = max(len(start_actives), 1)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_churn_denominador_renovando.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa (regressão)**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de falhas pré-existentes (3, em `test_shopee_upsert_additive.py`, não relacionadas). Nenhuma falha NOVA.

- [ ] **Step 6: Commit**

```bash
git add app/services/admin_metrics_service.py tests/unit/test_churn_denominador_renovando.py
git commit -m "fix(admin): denominador do churn usa renewing_subscribers, não active_subscribers"
```

### Task 3: Item 3 — MRR bruto usa preço de tabela

**Files:**
- Modify: `app/services/admin_metrics_service.py:390-404` (`mrr_cents`) e imports do topo do arquivo
- Test: `tests/unit/test_mrr_bruto_preco_de_tabela.py`

**Interfaces:**
- Consumes: `_normalize_plan_label(name, plan_id) -> str` (já existe no mesmo arquivo, linha 154); `list_price_cents(plan, frequency) -> Optional[int]` (`app/core/plans.py:89`, catálogo em `PLAN_LIST_PRICE_CENTS`, linha 71).
- Produces: `mrr_cents(actives=None) -> {"net": int, "gross": int}` — assinatura não muda; `gross` passa a vir do catálogo, `net` continua vindo da última cobrança paga.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 3: bruto do MRR usa preço de TABELA (list_price_cents),
não o valor real da última cobrança paga — que pode vir com desconto
histórico. Líquido continua vindo da cobrança real (não muda)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _ev(**kwargs):
    defaults = dict(
        id=1,
        subscription_id="sub-1",
        customer_email="a@example.com",
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="trimestral",
        amount_net_cents=12000,   # pagou com desconto — abaixo da tabela
        amount_gross_cents=12500,  # bruto real pago, também abaixo da tabela (14700)
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_bruto_usa_preco_de_tabela_nao_ultima_cobranca():
    svc = AdminMetricsService(MagicMock())
    assinante = _ev()
    svc._last_paid_for = lambda ev: assinante  # última cobrança = o próprio evento

    resultado = svc.mrr_cents(actives=[assinante])

    # Pro trimestral: tabela = 14700 cents / 3 = 4900 exato.
    assert resultado["gross"] == 4900
    # líquido continua vindo da cobrança real (12000 / 3 = 4000).
    assert resultado["net"] == 4000


def test_bruto_cai_no_valor_real_quando_plano_fora_do_catalogo():
    svc = AdminMetricsService(MagicMock())
    assinante = _ev(plan_name="Plano Descontinuado", plan_id="legado", amount_gross_cents=5000, amount_net_cents=4500)
    svc._last_paid_for = lambda ev: assinante

    resultado = svc.mrr_cents(actives=[assinante])

    # _normalize_plan_label("Plano Descontinuado", "legado") cai em "essencial"
    # por default — mas se não houver frequência reconhecida ou preço no
    # catálogo, list_price_cents pode retornar None; nesse caso o fallback é
    # o valor real pago (comportamento anterior), não zero.
    assert resultado["gross"] > 0
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_mrr_bruto_preco_de_tabela.py -v`
Expected: FAIL — `resultado["gross"]` vem 4166 (12500/3 arredondado), não 4900.

- [ ] **Step 3: Aplicar o fix**

Adicionar import no topo de `app/services/admin_metrics_service.py` (perto da linha 21, junto dos outros imports de `app.services`/`app.core`):

```python
from app.core.plans import list_price_cents
```

Em `mrr_cents` (linha 390-404), trocar:

```python
    def mrr_cents(self, actives: Optional[List[SubscriptionEvent]] = None) -> Dict[str, int]:
        actives = actives if actives is not None else self.renewing_subscribers()
        # Precisão cheia por assinante, arredonda só no total — dividir em inteiro
        # (`// div`) por assinante perde centavos a cada um antes de somar.
        net_frac = 0.0
        gross_frac = 0.0
        for ev in actives:
            div = _freq_divisor(ev.plan_frequency)
            # última cobrança paga da assinatura
            paid = self._last_paid_for(ev)
            n = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
            g = (paid.amount_gross_cents if paid else ev.amount_gross_cents) or 0
            net_frac += n / div
            gross_frac += g / div
        return {"net": int(round(net_frac)), "gross": int(round(gross_frac))}
```

por:

```python
    def mrr_cents(self, actives: Optional[List[SubscriptionEvent]] = None) -> Dict[str, int]:
        actives = actives if actives is not None else self.renewing_subscribers()
        # Precisão cheia por assinante, arredonda só no total — dividir em inteiro
        # (`// div`) por assinante perde centavos a cada um antes de somar.
        net_frac = 0.0
        gross_frac = 0.0
        for ev in actives:
            div = _freq_divisor(ev.plan_frequency)
            # última cobrança paga da assinatura
            paid = self._last_paid_for(ev)
            n = (paid.amount_net_cents if paid else ev.amount_net_cents) or 0
            # Bruto (Rodada 7, item 3) = preço de TABELA vigente, não a última
            # cobrança real — evita que desconto/cupom histórico distorça o
            # "faturamento potencial" que o bruto representa. Sem preço
            # cadastrado pro plano/frequência, cai no valor real pago.
            plano = _normalize_plan_label(ev.plan_name, ev.plan_id)
            tabela = list_price_cents(plano, ev.plan_frequency)
            g = tabela if tabela is not None else ((paid.amount_gross_cents if paid else ev.amount_gross_cents) or 0)
            net_frac += n / div
            gross_frac += g / div
        return {"net": int(round(net_frac)), "gross": int(round(gross_frac))}
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_mrr_bruto_preco_de_tabela.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de 3 falhas pré-existentes, nenhuma nova. Atenção especial a
`tests/unit/test_admin_metrics_service.py` e qualquer teste que confira
`mrr_gross_cents`/`dashboard()["mrr_gross_cents"]` com um valor fixo — se
algum quebrar, é porque assumia o comportamento antigo (última cobrança);
ajustar o valor esperado nesse teste pro preço de tabela correspondente,
não reverter o fix.

- [ ] **Step 6: Commit**

```bash
git add app/services/admin_metrics_service.py tests/unit/test_mrr_bruto_preco_de_tabela.py
git commit -m "fix(admin): bruto do MRR usa preço de tabela, não a última cobrança paga"
```

### Task 4: Item 5 — janela de período alinhada a dia civil BRT

**Files:**
- Modify: `app/services/platform_usage_service.py:103-108` (`_inicio`), `:180-201` (`usuarias_por_dia`), `:270-313` (`atividade_por_usuaria`)
- Test: `tests/unit/test_platform_usage_janela_brt.py`

**Interfaces:**
- Consumes: `BRT` (ZoneInfo) e `_brt_date(dt: datetime) -> date` de `app.services.admin_metrics_service` (import deferido, dentro do método — mesmo padrão já usado por `_base_ativa()` pra importar `AdminMetricsService`).
- Produces: `_inicio(periodo) -> datetime` (assinatura não muda); `usuarias_por_dia(periodo) -> List[{"date": str, "acessos": int, "usuarias": int}]` (mesma forma, bucketing agora em Python); `atividade_por_usuaria(periodo) -> List[Dict]` (mesma forma, `dias_ativos` agora conta dias civis BRT).

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 5: janela de período alinhada a dia civil BRT.

Antes, uma janela de "7d" cobria pedaços de até 8 datas (UTC, sem
alinhamento a meia-noite civil). Este teste fixa `agora` num horário da
tarde BRT e confere que os logins caem em no máximo 7 dias distintos.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from app.services.platform_usage_service import PlatformUsageService

BRT = ZoneInfo("America/Sao_Paulo")


def test_inicio_alinha_a_dia_civil_brt(monkeypatch):
    # 12/08/2026, 15h BRT (18h UTC) — meio da tarde, o pior caso pro bug antigo.
    agora_fixo = datetime(2026, 8, 12, 18, 0, tzinfo=timezone.utc)

    class _DatetimeFixo(datetime):
        @classmethod
        def now(cls, tz=None):
            return agora_fixo.astimezone(tz) if tz else agora_fixo

    monkeypatch.setattr(
        "app.services.platform_usage_service.datetime", _DatetimeFixo
    )

    svc = PlatformUsageService(MagicMock())
    inicio = svc._inicio("7d")

    # 7 dias = hoje (06/08 a 12/08 em BRT) + 6 anteriores — início é meia-noite
    # BRT de 06/08, convertida pra UTC (03h UTC).
    esperado = datetime(2026, 8, 6, 0, 0, tzinfo=BRT).astimezone(timezone.utc)
    assert inicio == esperado


def test_usuarias_por_dia_nao_estoura_o_numero_de_dias_da_janela():
    svc = PlatformUsageService(MagicMock())
    svc._ids_admin = lambda: []

    # 8 acessos espalhados por 7 dias civis BRT distintos, incluindo um às
    # 23h30 BRT (02h30 UTC do dia seguinte) — o caso que quebrava o cast em
    # UTC.
    logins = [
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 6, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 7, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 8, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 9, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)),
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)),
        # 23h30 BRT de 11/08 = 02h30 UTC de 12/08 — dia civil BRT ainda é 11/08.
        SimpleNamespace(user_id=1, logged_at=datetime(2026, 8, 12, 2, 30, tzinfo=timezone.utc)),
    ]
    query_mock = MagicMock()
    query_mock.with_entities.return_value.all.return_value = [
        (l.logged_at, l.user_id) for l in logins
    ]
    svc._logins_do_periodo = lambda periodo: query_mock

    dias = svc.usuarias_por_dia("7d")

    datas_distintas = {d["date"] for d in dias}
    assert len(datas_distintas) == 6  # não 7 datas UTC diferentes — 11/08 absorve o registro das 2h30
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_platform_usage_janela_brt.py -v`
Expected: FAIL — `_inicio` ainda retorna um instante não alinhado a meia-noite BRT; `usuarias_por_dia` ainda espera uma query SQL com `.with_entities(...).group_by(...)`, não `.with_entities(...).all()`.

- [ ] **Step 3: Aplicar o fix em `_inicio`**

Em `app/services/platform_usage_service.py`, trocar (linhas 103-108):

```python
    def _inicio(self, periodo: str) -> datetime:
        dias = PERIODOS_VALIDOS.get(periodo, 7)
        agora = datetime.now(timezone.utc)
        if periodo == "hoje":
            return agora.replace(hour=0, minute=0, second=0, microsecond=0)
        return agora - timedelta(days=dias)
```

por:

```python
    def _inicio(self, periodo: str) -> datetime:
        """Início da janela alinhado a dia civil BRT (Rodada 7, item 5).

        7d = hoje (dia civil BRT) + 6 anteriores, do início do dia mais
        antigo até agora — não um instante `agora - N dias` (que corta no
        meio de um dia e espalha por N+1 datas quando agrupado por dia).
        """
        from app.services.admin_metrics_service import BRT

        dias = PERIODOS_VALIDOS.get(periodo, 7)
        agora_brt = datetime.now(timezone.utc).astimezone(BRT)
        if periodo == "hoje":
            inicio_brt = agora_brt.replace(hour=0, minute=0, second=0, microsecond=0)
        else:
            inicio_brt = (agora_brt - timedelta(days=dias - 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        return inicio_brt.astimezone(timezone.utc)
```

- [ ] **Step 4: Aplicar o fix em `usuarias_por_dia`**

Trocar (linhas 180-201):

```python
    def usuarias_por_dia(self, periodo: str) -> List[Dict[str, Any]]:
        """Acessos (hits) e pessoas distintas por dia.

        `acessos` é a série principal do gráfico (barras); `usuarias` é a linha
        de adoção. Sem as duas, o gráfico da aba Uso fica só com a linha —
        exatamente o que faltou na rodada 5.
        """
        linhas = (
            self._logins_do_periodo(periodo)
            .with_entities(
                cast(UserLogin.logged_at, Date).label("d"),
                func.count().label("acessos"),
                func.count(func.distinct(UserLogin.user_id)).label("usuarias"),
            )
            .group_by("d")
            .order_by("d")
            .all()
        )
        return [
            {"date": str(d), "acessos": acessos, "usuarias": usuarias}
            for d, acessos, usuarias in linhas
        ]
```

por:

```python
    def usuarias_por_dia(self, periodo: str) -> List[Dict[str, Any]]:
        """Acessos (hits) e pessoas distintas por dia civil BRT.

        `acessos` é a série principal do gráfico (barras); `usuarias` é a
        linha de adoção. Bucketing em Python (Rodada 7, item 5) — `cast(...,
        Date)` no Postgres trunca pelo fuso da SESSÃO, não BRT; mesmo padrão
        de `_brt_date` já usado em admin_metrics_service.py.
        """
        from app.services.admin_metrics_service import _brt_date

        linhas = (
            self._logins_do_periodo(periodo)
            .with_entities(UserLogin.logged_at, UserLogin.user_id)
            .all()
        )
        por_dia: Dict[Any, Dict[str, Any]] = {}
        for logged_at, user_id in linhas:
            if logged_at.tzinfo is None:
                logged_at = logged_at.replace(tzinfo=timezone.utc)
            d = _brt_date(logged_at)
            bucket = por_dia.setdefault(d, {"acessos": 0, "usuarias": set()})
            bucket["acessos"] += 1
            bucket["usuarias"].add(user_id)
        return [
            {"date": str(d), "acessos": v["acessos"], "usuarias": len(v["usuarias"])}
            for d, v in sorted(por_dia.items())
        ]
```

- [ ] **Step 5: Aplicar o fix em `atividade_por_usuaria`**

Trocar (linhas 270-282, só o bloco de query e agregação — o resto da
função, de `if not linhas:` em diante, não muda):

```python
    def atividade_por_usuaria(self, periodo: str) -> List[Dict[str, Any]]:
        linhas = (
            self._logins_do_periodo(periodo)
            .with_entities(
                UserLogin.user_id,
                func.count().label("acessos"),
                func.count(func.distinct(cast(UserLogin.logged_at, Date))).label("dias"),
                func.max(UserLogin.logged_at).label("ultimo"),
            )
            .group_by(UserLogin.user_id)
            .order_by(func.count().desc())
            .all()
        )
```

por:

```python
    def atividade_por_usuaria(self, periodo: str) -> List[Dict[str, Any]]:
        from app.services.admin_metrics_service import _brt_date

        brutas = (
            self._logins_do_periodo(periodo)
            .with_entities(UserLogin.user_id, UserLogin.logged_at)
            .all()
        )
        por_usuario: Dict[int, Dict[str, Any]] = {}
        for uid, logged_at in brutas:
            if logged_at.tzinfo is None:
                logged_at = logged_at.replace(tzinfo=timezone.utc)
            info = por_usuario.setdefault(uid, {"acessos": 0, "dias": set(), "ultimo": logged_at})
            info["acessos"] += 1
            info["dias"].add(_brt_date(logged_at))
            if logged_at > info["ultimo"]:
                info["ultimo"] = logged_at
        linhas = sorted(
            (
                (uid, v["acessos"], len(v["dias"]), v["ultimo"])
                for uid, v in por_usuario.items()
            ),
            key=lambda row: row[1],
            reverse=True,
        )
```

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_platform_usage_janela_brt.py -v`
Expected: PASS

- [ ] **Step 7: Rodar a suíte de platform_usage completa (regressão)**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_platform_usage_links_paginas.py tests/unit/test_platform_usage_base_ativa.py -v`
Expected: PASS. Se `test_platform_usage_links_paginas.py` (que já mocka
`cast` via `@compiles(Cast, "sqlite")` pra testar `usuarias_por_dia`)
quebrar por causa da mudança de SQL pra Python, é sinal de que os testes
antigos afirmavam algo sobre a query SQL — ajustar as fixtures desse
arquivo pra assertarem o resultado final (`usuarias_por_dia()` retornado),
não a forma da query.

- [ ] **Step 8: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de 3 falhas pré-existentes, nenhuma nova.

- [ ] **Step 9: Commit**

```bash
git add app/services/platform_usage_service.py tests/unit/test_platform_usage_janela_brt.py
git commit -m "fix(admin): janela de período (7d/30d/90d) alinhada a dia civil BRT"
```

### Task 5: Item 9 (backend) — `atividade_por_usuaria` retorna o plano

**Depends on:** Task 4 (reescreve a mesma função).

**Files:**
- Modify: `app/services/platform_usage_service.py:270-313` (`atividade_por_usuaria`, versão pós-Task 4)
- Test: `tests/unit/test_atividade_usuaria_plano.py`

**Interfaces:**
- Consumes: `AdminMetricsService(db).active_subscribers() -> List[SubscriptionEvent]` (import deferido, mesmo padrão de `_base_ativa`); `_normalize_plan_label(name, plan_id) -> str` (`admin_metrics_service.py:154`).
- Produces: cada item de `atividade_por_usuaria()` ganha a chave `"plan": Optional[str]`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 9 (backend): atividade_por_usuaria expõe o plano de cada
usuária, pra o frontend decidir "0/0" (Pro sem uso) vs "—" (Essencial, sem
o recurso)."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.platform_usage_service import PlatformUsageService


def test_atividade_por_usuaria_inclui_plano(monkeypatch):
    svc = PlatformUsageService(MagicMock())
    svc._ids_admin = lambda: []

    login = SimpleNamespace(user_id=7, logged_at=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc))
    query_mock = MagicMock()
    query_mock.with_entities.return_value.all.return_value = [(7, login.logged_at)]
    svc._logins_do_periodo = lambda periodo: query_mock
    svc.uso_de_links_e_paginas = lambda periodo, ids: {}

    subscriber = SimpleNamespace(user_id=7, plan_name="Essencial", plan_id="essencial")

    class _AdminMetricsServiceFake:
        def __init__(self, db):
            pass

        def active_subscribers(self):
            return [subscriber]

    monkeypatch.setattr(
        "app.services.admin_metrics_service.AdminMetricsService", _AdminMetricsServiceFake
    )
    # nomes/emails vêm de `dict(self.db.query(User.id, User.name).filter(...))`
    # — uma lista vazia é um iterável válido de pares pro dict(), sem precisar
    # mockar __iter__ em cima do MagicMock (que não funciona por atribuição
    # direta de instância; magic methods são resolvidos pelo tipo).
    svc.db.query.return_value.filter.return_value = []

    linhas = svc.atividade_por_usuaria("7d")

    assert linhas[0]["plan"] == "essencial"
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_atividade_usuaria_plano.py -v`
Expected: FAIL — `KeyError: 'plan'`.

- [ ] **Step 3: Aplicar o fix**

Em `atividade_por_usuaria` (pós-Task 4), depois do bloco `ids = [l[0] for l in linhas]` / `uso = self.uso_de_links_e_paginas(periodo, ids)`, adicionar a busca de plano e incluir no dict de retorno:

```python
        ids = [l[0] for l in linhas]
        uso = self.uso_de_links_e_paginas(periodo, ids)

        from app.services.admin_metrics_service import AdminMetricsService, _normalize_plan_label

        planos = {
            ev.user_id: _normalize_plan_label(ev.plan_name, ev.plan_id)
            for ev in AdminMetricsService(self.db).active_subscribers()
            if ev.user_id in set(ids)
        }
        return [
            {
                "user_id": uid,
                "nome": capitalizar_nome(nomes.get(uid)) or emails.get(uid) or f"#{uid}",
                "email": emails.get(uid),
                "plan": planos.get(uid),
                "acessos": acessos,
                "dias_ativos": dias,
                "ultimo_acesso": ultimo.isoformat() if ultimo else None,
                **uso.get(uid, {
                    "links_em_uso": 0,
                    "links_criados": 0,
                    "paginas_em_uso": 0,
                    "paginas_criadas": 0,
                }),
            }
            for uid, acessos, dias, ultimo in linhas
        ]
```

(Remove o `return [...]` antigo que não tinha `"plan"`.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_atividade_usuaria_plano.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline, nenhuma falha nova.

- [ ] **Step 6: Commit**

```bash
git add app/services/platform_usage_service.py tests/unit/test_atividade_usuaria_plano.py
git commit -m "feat(admin): atividade_por_usuaria expõe o plano da usuária"
```

### Task 6: Item 8 (backend) — CSV exporta com os filtros aplicados

**Files:**
- Modify: `app/api/v1/routes/admin_panel.py:127-132` (`export_clients_csv`)
- Test: `tests/unit/test_export_csv_com_filtros.py`

**Interfaces:**
- Consumes: `AdminMetricsService.list_clients(filters: dict) -> List[Dict]` (já existe).
- Produces: `GET /admin/clients/export.csv` passa a aceitar os mesmos query params de `GET /admin/clients` (`q`, `status`, `plan`, `expiring_7d`, `payment_failed`, `never_connected`, `no_login_10d`).

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 8 (backend): export.csv usa os mesmos filtros da lista,
não a base inteira sem filtro."""
from starlette.testclient import TestClient

from app.main import app
from app.api.v1.dependencies import get_current_user, require_admin
from app.db.session import get_db


def test_export_csv_repassa_filtros_para_list_clients(monkeypatch):
    capturado = {}

    class _FakeSvc:
        def __init__(self, db):
            pass

        def list_clients(self, filters):
            capturado["filters"] = filters
            return []

    monkeypatch.setattr("app.api.v1.routes.admin_panel.AdminMetricsService", _FakeSvc)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[require_admin] = lambda: object()
    try:
        client = TestClient(app)
        client.get("/api/v1/admin/clients/export.csv?status=inativo&plan=pro&no_login_10d=true")
    finally:
        app.dependency_overrides.clear()

    assert capturado["filters"]["status"] == "inativo"
    assert capturado["filters"]["plan"] == "pro"
    assert capturado["filters"]["no_login_10d"] is True
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_export_csv_com_filtros.py -v`
Expected: FAIL — `capturado["filters"] == {}` (hoje `export_clients_csv` chama `list_clients({})` sempre).

- [ ] **Step 3: Aplicar o fix**

Em `app/api/v1/routes/admin_panel.py`, trocar a assinatura de `export_clients_csv` (linha 127-132) — mesmos parâmetros de `admin_clients` (linha 88-99):

```python
@router.get("/clients/export.csv")
def export_clients_csv(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = AdminMetricsService(db).list_clients({})
```

por:

```python
@router.get("/clients/export.csv")
def export_clients_csv(
    q: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    plan: Optional[str] = None,
    expiring_7d: bool = False,
    payment_failed: bool = False,
    never_connected: bool = False,
    no_login_10d: bool = False,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = AdminMetricsService(db).list_clients({
        "q": q,
        "status": status_filter,
        "plan": plan,
        "expiring_7d": expiring_7d,
        "payment_failed": payment_failed,
        "never_connected": never_connected,
        "no_login_10d": no_login_10d,
    })
```

(`Optional` e `Query` já estão importados no arquivo — usados em
`admin_clients` logo acima.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_export_csv_com_filtros.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline, nenhuma falha nova.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/routes/admin_panel.py tests/unit/test_export_csv_com_filtros.py
git commit -m "fix(admin): CSV de clientes exporta com os mesmos filtros da lista"
```

### Task 7: Item 4 — login legado usa `record_access` (dedupe)

**Files:**
- Modify: `app/api/v1/routes/auth.py:29-51` (rota `POST /login`)
- Test: `tests/unit/test_login_legado_dedupe.py`

**Interfaces:**
- Consumes: `record_access(db, user_id, ip=None, user_agent=None) -> bool` (já existe, `app/services/daily_access_service.py:26`, janela de dedupe de 2 minutos).

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 4: login legado (fallback pré-Supabase) passa a usar
record_access() — mesma janela de dedupe de 2min do fluxo principal.
Hoje grava UserLogin direto, sem proteção nenhuma."""
from unittest.mock import MagicMock, patch

from app.api.v1.routes import auth as auth_routes


def test_login_legado_usa_record_access_nao_insert_direto():
    with patch.object(auth_routes, "AuthService") as AuthServiceMock:
        AuthServiceMock.return_value.login.return_value = {
            "user": MagicMock(id=42),
        }
        with patch("app.services.daily_access_service.record_access") as record_access_mock:
            db = MagicMock()
            http_request = MagicMock()
            http_request.client.host = "1.2.3.4"
            http_request.headers.get.return_value = "pytest-agent"

            from app.schemas.user import LoginRequest

            auth_routes.login(
                LoginRequest(email="a@example.com", password="x"),
                http_request,
                db,
            )

            record_access_mock.assert_called_once_with(
                db, 42, ip="1.2.3.4", user_agent="pytest-agent"
            )
            db.add.assert_not_called()  # não grava UserLogin direto mais
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_login_legado_dedupe.py -v`
Expected: FAIL — `record_access` nunca é chamado; `db.add` é chamado direto com um `UserLogin`.

- [ ] **Step 3: Aplicar o fix**

Em `app/api/v1/routes/auth.py`, trocar o bloco (linhas 38-51):

```python
    # Login efetivo — não refresh de token
    try:
        from app.models.user_login import UserLogin

        user = result.get("user") if isinstance(result, dict) else getattr(result, "user", None)
        uid = getattr(user, "id", None)
        if uid:
            ip = http_request.client.host if http_request.client else None
            ua = http_request.headers.get("user-agent")
            db.add(UserLogin(user_id=uid, ip=ip, user_agent=ua))
            db.commit()
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
    return result
```

por:

```python
    # Login efetivo — não refresh de token. record_access() já tem a janela
    # de dedupe de 2min (Rodada 7, item 4: este caminho legado gravava direto,
    # sem essa proteção — inflava contagem de acesso pra quem cai aqui).
    try:
        from app.services.daily_access_service import record_access

        user = result.get("user") if isinstance(result, dict) else getattr(result, "user", None)
        uid = getattr(user, "id", None)
        if uid:
            ip = http_request.client.host if http_request.client else None
            ua = http_request.headers.get("user-agent")
            record_access(db, uid, ip=ip, user_agent=ua)
    except Exception:  # noqa: BLE001
        try:
            db.rollback()
        except Exception:
            pass
    return result
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_login_legado_dedupe.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline, nenhuma falha nova.

- [ ] **Step 6: Documentar o achado do diagnóstico (Task 1) no relatório**

Anexar ao relatório desta task a contagem de "pares < 2min" que o
diagnóstico da Task 1 encontrou para Daniele especificamente — isso
quantifica o quanto deste fix explica os 126 acessos em 8 dias
reportados no brief. Se o diagnóstico não encontrou nenhum par pra ela
(bug pode estar em outro lugar), registrar isso explicitamente — não
assumir que o fix resolve o caso dela sem a evidência.

- [ ] **Step 7: Commit**

```bash
git add app/api/v1/routes/auth.py tests/unit/test_login_legado_dedupe.py
git commit -m "fix(auth): login legado usa record_access, ganha janela de dedupe de 2min"
```

**Nota (teste manual pedido pelo brief):** o "teste da 1 hora" ("usar o
app normalmente por 1 hora, sem deslogar → deve gerar 1 registro") é um
teste de uso real, não automatizável em pytest. Fazer manualmente contra
o ambiente de hml antes de fechar a rodada, e reportar o resultado
(quantos registros de `UserLogin` a sessão gerou) no relatório final
(Task 15).

### Task 8: Item 6 — margin nos gráficos (label do MRR cortado)

**Files:**
- Modify: `src/features/admin/components/chart-defaults.tsx`
- Modify: `src/features/admin/pages/AdminDashboard.tsx:162,187`

**Interfaces:**
- Produces: `CHART_MARGIN` — exportado de `chart-defaults.tsx`, consumido por `AdminDashboard.tsx`.

- [ ] **Step 1: Adicionar `CHART_MARGIN` em `chart-defaults.tsx`**

No fim do arquivo `src/features/admin/components/chart-defaults.tsx`, depois de `VALUE_LABEL_PROPS`:

```tsx
/** Respiro à direita — sem margin, o label do último ponto/barra encosta
 * na borda do viewBox do SVG e é cortado (Rodada 7, item 6). */
export const CHART_MARGIN = { top: 20, right: 28, left: 0, bottom: 0 } as const;
```

- [ ] **Step 2: Aplicar em `AdminDashboard.tsx`**

Importar `CHART_MARGIN` junto dos outros imports de `chart-defaults` (linha 25-30):

```tsx
import {
  AXIS_PROPS,
  BAR_CURSOR,
  LINE_CURSOR,
  VALUE_LABEL_PROPS,
  CHART_MARGIN,
} from "@/features/admin/components/chart-defaults";
```

Em `<LineChart data={mrrSeries}>` (linha 162), trocar para:

```tsx
<LineChart data={mrrSeries} margin={CHART_MARGIN}>
```

Em `<BarChart data={revSeries}>` (linha 187), trocar para:

```tsx
<BarChart data={revSeries} margin={CHART_MARGIN}>
```

- [ ] **Step 3: Verificação visual (manual, não há teste automatizado pra layout)**

Run: `npm run dev`, abrir `/admin` (Dashboard), conferir que o label do
último ponto do gráfico "MRR — últimos 12 meses" e da última barra de
"Faturamento líquido — 12 meses" aparece por inteiro, sem cortar na borda
direita do card.

- [ ] **Step 4: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos.

- [ ] **Step 5: Commit**

```bash
git add src/features/admin/components/chart-defaults.tsx src/features/admin/pages/AdminDashboard.tsx
git commit -m "fix(admin): margin nos gráficos de MRR/Faturamento — label do último ponto não corta mais"
```

### Task 9: Item 7 — Dashboard em grid 2×2

**Files:**
- Modify: `src/features/admin/pages/AdminDashboard.tsx:206,226`

- [ ] **Step 1: Remover `lg:col-span-2` dos dois últimos cards**

Em `src/features/admin/pages/AdminDashboard.tsx`, trocar (linha 206):

```tsx
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Novas × canceladas por mês</CardTitle>
```

por:

```tsx
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Novas × canceladas por mês</CardTitle>
```

E (linha 226):

```tsx
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Plano × periodicidade</CardTitle>
```

por:

```tsx
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Plano × periodicidade</CardTitle>
```

- [ ] **Step 2: Verificação visual**

Run: `npm run dev`, abrir `/admin` em viewport `lg:` (≥1024px) e conferir
grid 2×2: MRR + Faturamento na primeira linha, Novas×Canceladas +
Plano×periodicidade na segunda, cada card ocupando metade da largura.
Conferir também em mobile (`<1024px`) que os 4 cards continuam empilhados
em coluna única (o grid já é `sm:grid-cols-2 lg:grid-cols-2` via a classe
existente `grid gap-4 lg:grid-cols-2` — sem mudança de comportamento
abaixo de `lg:`).

- [ ] **Step 3: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos.

- [ ] **Step 4: Commit**

```bash
git add src/features/admin/pages/AdminDashboard.tsx
git commit -m "fix(admin): Dashboard em grid 2x2 — Novas×Canceladas e Plano×periodicidade não esticam mais"
```

### Task 10: Item 8 (frontend) — paginação da lista de Clientes

**Depends on:** Task 6 (endpoint de CSV já aceita filtros).

**Files:**
- Modify: `src/features/admin/components/AdminTableFooter.tsx` (formato "Mostrando X–Y de Z")
- Modify: `src/features/admin/pages/AdminClients.tsx` (paginação, reset de página, CSV com filtros)

**Interfaces:**
- Consumes: `paginar<T>(itens, pagina, porPagina?) -> T[]`, `Paginacao` (`AdminTableFooter.tsx`, já existem).

- [ ] **Step 1: Adicionar o formato "intervalo" em `Paginacao`**

Em `src/features/admin/components/AdminTableFooter.tsx`, trocar a
assinatura e o corpo de `Paginacao` (linhas 15-48):

```tsx
export function Paginacao({
  pagina,
  total,
  onChange,
  porPagina = LINHAS_POR_PAGINA,
}: {
  pagina: number;
  total: number;
  onChange: (p: number) => void;
  porPagina?: number;
}) {
  const paginas = totalDePaginas(total, porPagina);
  if (paginas <= 1) return null;
  return (
    <div className="mt-4 flex items-center justify-between text-sm">
      <span className="text-muted-foreground">
        {total} {total === 1 ? "registro" : "registros"} · página {pagina} de {paginas}
      </span>
```

por:

```tsx
export function Paginacao({
  pagina,
  total,
  onChange,
  porPagina = LINHAS_POR_PAGINA,
  formato = "registros",
}: {
  pagina: number;
  total: number;
  onChange: (p: number) => void;
  porPagina?: number;
  formato?: "registros" | "intervalo";
}) {
  const paginas = totalDePaginas(total, porPagina);
  if (paginas <= 1) return null;
  const inicio = (pagina - 1) * porPagina + 1;
  const fim = Math.min(pagina * porPagina, total);
  return (
    <div className="mt-4 flex items-center justify-between text-sm">
      <span className="text-muted-foreground">
        {formato === "intervalo"
          ? `Mostrando ${inicio}–${fim} de ${total}`
          : `${total} ${total === 1 ? "registro" : "registros"} · página ${pagina} de ${paginas}`}
      </span>
```

(O resto do componente — os dois `<Button>` de Anterior/Próxima — não
muda. `AdminSyncStatus.tsx`, que já usa `Paginacao` sem passar `formato`,
continua no comportamento atual por default.)

- [ ] **Step 2: Adicionar paginação em `AdminClients.tsx`**

Importar `paginar`, `Paginacao` no topo do arquivo (junto dos outros
imports de `@/features/admin/components`):

```tsx
import { paginar, Paginacao } from "@/features/admin/components/AdminTableFooter";
```

Adicionar estado de página (junto aos outros `useState`, perto da linha
132-134):

```tsx
  const [pagina, setPagina] = useState(1);
```

No `useEffect` que reage a `filters` (linhas 149-158), resetar a página:

```tsx
  useEffect(() => {
    setLoading(true);
    setPagina(1);
    fetchAdminClients(filters)
      .then((data) => {
        setRows(data);
        setError(null);
      })
      .catch((e) => setError(e instanceof Error ? e.message : "Erro"))
      .finally(() => setLoading(false));
  }, [filters]);
```

Em `toggleSort` (linhas 171-178), resetar a página também:

```tsx
  const toggleSort = (key: SortKey) => {
    setPagina(1);
    if (key === sortKey) {
      setSortAsc((v) => !v);
      return;
    }
    setSortKey(key);
    setSortAsc(true);
  };
```

Adicionar a fatia paginada logo depois de `sortedRows` (linha 169), e
trocar o `.map` da tabela e o `colSpan` de "Nenhum cliente encontrado"
pra usar essa fatia:

```tsx
  const linhasPagina = useMemo(
    () => paginar(sortedRows, pagina),
    [sortedRows, pagina],
  );
```

Na renderização, trocar `sortedRows.map((r, i) => (` (linha 302) por
`linhasPagina.map((r, i) => (`, e `sortedRows.length === 0` (linha 341)
por `linhasPagina.length === 0`. Depois do `</Table>` (fechamento do
`<TableBody>`, antes do `</div>` do `min-w-[960px]`), adicionar:

```tsx
            <Paginacao
              pagina={pagina}
              total={sortedRows.length}
              onChange={setPagina}
              formato="intervalo"
            />
```

- [ ] **Step 3: CSV exporta com os filtros ativos**

Trocar `exportCsv` (linhas 180-189):

```tsx
  const exportCsv = async () => {
    const res = await fetchWithAuth(getApiUrl("/api/v1/admin/clients/export.csv"));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "admin_clients.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
```

por:

```tsx
  const exportCsv = async () => {
    const qs = new URLSearchParams();
    Object.entries(filters).forEach(([k, v]) => {
      if (v === undefined || v === "" || v === false) return;
      qs.set(k, String(v));
    });
    const res = await fetchWithAuth(getApiUrl(`/api/v1/admin/clients/export.csv?${qs}`));
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "admin_clients.csv";
    a.click();
    URL.revokeObjectURL(url);
  };
```

- [ ] **Step 4: Verificação manual (busca ignora paginação, filtro/ordenação voltam pra página 1)**

Run: `npm run dev`, abrir `/admin/clientes`:
1. Confirmar que com >20 clientes aparece "Mostrando 1–20 de N" no rodapé.
2. Ir pra página 2, digitar algo na busca — confirmar que volta pra página 1 e busca no total (não só nos 20 visíveis antes).
3. Ir pra página 2, clicar em "Ordenar por Nome" — confirmar que volta pra página 1.
4. Trocar o filtro de Status — confirmar que volta pra página 1.
5. Aplicar um filtro (ex.: Status = Inativo) e exportar CSV — abrir o arquivo e confirmar que só tem os clientes inativos, não a base inteira.

- [ ] **Step 5: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos.

- [ ] **Step 6: Commit**

```bash
git add src/features/admin/components/AdminTableFooter.tsx src/features/admin/pages/AdminClients.tsx
git commit -m "feat(admin): paginação na lista de Clientes (20/página) e CSV com os filtros ativos"
```

### Task 11: Item 9 (frontend) — "—" para recurso fora do plano

**Depends on:** Task 5 (backend expõe `plan` em `PlatformUsage.atividade`).

**Files:**
- Modify: `src/services/admin-panel.service.ts` (tipo `PlatformUsage.atividade`)
- Modify: `src/features/admin/components/PlatformUsageTab.tsx:232-237`
- Modify: `src/features/admin/pages/AdminClientDetail.tsx:203-207`

**Interfaces:**
- Consumes: `planLimit(plan, "links" | "paginas_captura") -> number` (`src/shared/lib/plans.ts:97`, já existe — retorna `0` quando o plano não tem o recurso).

- [ ] **Step 1: Adicionar `plan` ao tipo `PlatformUsage.atividade`**

Em `src/services/admin-panel.service.ts`, dentro de `PlatformUsage.atividade`
(linhas 266-277), adicionar o campo:

```tsx
  atividade: {
    user_id: number;
    nome: string;
    email: string | null;
    plan: string | null;
    acessos: number;
    dias_ativos: number;
    links_em_uso: number;
    links_criados: number;
    paginas_em_uso: number;
    paginas_criadas: number;
    ultimo_acesso: string | null;
  }[];
```

- [ ] **Step 2: Aplicar em `PlatformUsageTab.tsx`**

Importar `planLimit` no topo do arquivo:

```tsx
import { planLimit } from "@/shared/lib/plans";
```

Trocar as duas células de Links/Páginas (linhas 232-237):

```tsx
                      <TableCell className="text-right tabular-nums">
                        {u.links_em_uso}/{u.links_criados}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {u.paginas_em_uso}/{u.paginas_criadas}
                      </TableCell>
```

por:

```tsx
                      <TableCell className="text-right tabular-nums">
                        {planLimit(u.plan, "links") === 0 ? "—" : `${u.links_em_uso}/${u.links_criados}`}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {planLimit(u.plan, "paginas_captura") === 0
                          ? "—"
                          : `${u.paginas_em_uso}/${u.paginas_criadas}`}
                      </TableCell>
```

- [ ] **Step 3: Aplicar em `AdminClientDetail.tsx`**

Importar `planLimit` no topo do arquivo (se ainda não estiver importado
de `@/shared/lib/plans`).

Trocar o parágrafo de Links/Páginas (linhas 203-207):

```tsx
            <p>
              Links: {data.usage?.links_em_uso ?? 0} em uso / {data.usage?.links_criados ?? 0} criados
              {" · "}
              Páginas: {data.usage?.paginas_em_uso ?? 0}/{data.usage?.paginas_criadas ?? 0}
            </p>
```

por:

```tsx
            <p>
              Links:{" "}
              {planLimit(sub.plan || data.plan, "links") === 0
                ? "— (fora do plano)"
                : `${data.usage?.links_em_uso ?? 0} em uso / ${data.usage?.links_criados ?? 0} criados`}
              {" · "}
              Páginas:{" "}
              {planLimit(sub.plan || data.plan, "paginas_captura") === 0
                ? "—"
                : `${data.usage?.paginas_em_uso ?? 0}/${data.usage?.paginas_criadas ?? 0}`}
            </p>
```

- [ ] **Step 4: Verificação manual**

Run: `npm run dev`, abrir a aba Uso (`/admin` → Uso) e conferir que
usuárias do plano Essencial mostram "—" em Links e Páginas, enquanto
usuárias Pro continuam mostrando "X/Y". Repetir na ficha individual
(`/admin/clientes/:id`) de uma usuária Essencial.

- [ ] **Step 5: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos.

- [ ] **Step 6: Commit**

```bash
git add src/services/admin-panel.service.ts src/features/admin/components/PlatformUsageTab.tsx src/features/admin/pages/AdminClientDetail.tsx
git commit -m "fix(admin): Essencial mostra — em Links/Páginas em vez de 0/0 (recurso fora do plano)"
```

### Task 12: Item 10 (frontend) — link do card corrigido + chip de filtro nomeado

**Files:**
- Modify: `src/features/admin/components/PlatformUsageTab.tsx:146-152`
- Modify: `src/features/admin/pages/AdminClients.tsx` (chip nomeado em vez de "Limpar alerta" genérico)

**Interfaces:**
- Consumes: `no_login_10d` já lido em `AdminClientsPage.filters` (linha 144) — nenhuma mudança de leitura necessária, só o link que aponta pra lá.

- [ ] **Step 1: Corrigir o link do card**

Em `src/features/admin/components/PlatformUsageTab.tsx`, trocar (linha 146):

```tsx
            <Link to={`/admin/clientes?sem_acesso=${data.cards.dias_sem_acesso}`}>
```

por:

```tsx
            <Link to="/admin/clientes?no_login_10d=1">
```

- [ ] **Step 2: Chip de filtro nomeado em `AdminClients.tsx`**

O botão "Limpar alerta" (linhas 238-242) já existe e já é removível — só
não diz QUAL filtro está ativo, o que o brief pede explicitamente ("chip
removível com ×"). Adicionar um mapa de rótulos e trocar o botão genérico
por um badge nomeado por filtro ativo.

Adicionar a constante, perto de `STATUS_PADRAO` (linha 74):

```tsx
const ALERT_FILTER_LABELS: Record<string, string> = {
  expiring_7d: "Vencendo em 7 dias",
  payment_failed: "Pagamento falhou",
  never_connected: "Nunca conectou",
  no_login_10d: "Sem acesso há 10d+",
};
```

Trocar o bloco `{hasAlertFilter && (...)}` (linhas 238-242):

```tsx
        {hasAlertFilter && (
          <Button variant="outline" size="sm" onClick={clearAlertFilters}>
            Limpar alerta
          </Button>
        )}
```

por:

```tsx
        {(["expiring_7d", "payment_failed", "never_connected", "no_login_10d"] as const)
          .filter((k) => filters[k])
          .map((k) => (
            <Badge key={k} variant="secondary" className="gap-1.5 py-1.5 pl-2.5 pr-1.5">
              {ALERT_FILTER_LABELS[k]}
              <button
                type="button"
                aria-label={`Remover filtro ${ALERT_FILTER_LABELS[k]}`}
                className="ml-0.5 rounded-full p-0.5 hover:bg-muted"
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.delete(k);
                  setParams(next);
                }}
              >
                <X className="h-3 w-3" />
              </button>
            </Badge>
          ))}
```

Adicionar os imports necessários no topo do arquivo — `Badge` já está
importado (linha 4); adicionar `X` ao import de `lucide-react` (linha 3):

```tsx
import { ArrowUpDown, Download, Loader2, Search, X } from "lucide-react";
```

`clearAlertFilters` e `hasAlertFilter` (linhas 191-200) ficam sem uso
depois dessa troca — remover as duas declarações.

- [ ] **Step 3: Verificação manual (número do card bate com a lista)**

Run: `npm run dev`. Na aba Uso, anotar o número do card "Sem acesso há
10d+". Clicar no card, confirmar que abre `/admin/clientes` com um chip
"Sem acesso há 10d+ ×" visível, e que o número de linhas da tabela bate
exatamente com o número do card (a população-base já deveria bater, já
que `STATUS_PADRAO` — aplicado por default — já restringe a
ativo/atrasado/cancelado_com_acesso, o mesmo universo de `_base_ativa()`
no backend). Se os números NÃO baterem, é sinal de que a nuance descrita
no design doc é real — reportar no relatório da task e não marcar o item
como fechado sem confirmar isso.

- [ ] **Step 4: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos, sem import não usado (`clearAlertFilters`/`hasAlertFilter` removidos por completo).

- [ ] **Step 5: Commit**

```bash
git add src/features/admin/components/PlatformUsageTab.tsx src/features/admin/pages/AdminClients.tsx
git commit -m "fix(admin): card Sem acesso 10d+ abre a lista já filtrada, com chip nomeado e removível"
```

---

## Fase 3 — Item 1 (Grupo B, decisão pós-diagnóstico)

### Task 13: Aplicar o achado do diagnóstico do item 1 (novas de julho)

**Depends on:** Task 1 (resultado do diagnóstico).

Esta task tem DUAS ramificações concretas — seguir a que o resultado da
Task 1 indicar. Não aplicar as duas.

**Ramo A — `new_subscriptions(2026, 7)` já retornou 8:**

- [ ] **Step 1:** Nenhuma mudança de código. Documentar no relatório da
  task: "Item 1 conferido contra produção em [data] — `new_subscriptions`
  já retorna 8 pra julho/2026, código correto desde antes desta rodada.
  Sem fix necessário."
- [ ] **Step 2:** Adicionar ao `CHANGELOG.md` (seção da Rodada 7, Task 15)
  uma nota de "Known issues" NEGATIVA — registrar que o item foi
  investigado e o código já estava certo, pra não reabrir a mesma dúvida
  numa rodada futura.

**Ramo B — `new_subscriptions(2026, 7)` retornou 6, e há linhas com
`is_plan_change` NULL em julho:**

**Files:**
- Modify: `app/services/admin_metrics_service.py:478-507` (`new_subscriptions`)
- Test: `tests/unit/test_novas_subscricoes_is_plan_change_nulo.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada 7, item 1: new_subscriptions() não pode perder assinantes cuja
linha paga tem is_plan_change NULL (em vez de False) — NULL não é
"mudança de plano", é dado sem essa marcação preenchida."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        subscription_id="sub-1",
        customer_email="a@example.com",
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        is_plan_change=None,  # NULL, não False
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_conta_assinante_com_is_plan_change_nulo():
    svc = AdminMetricsService(MagicMock())
    ev = _ev()

    # Simula a query filtrada do mês (primeiro .all()) e a query completa
    # de "primeiro pago" (segundo .all()) retornando o mesmo evento.
    svc.db.query.return_value.filter.return_value.all.side_effect = [[ev], [ev]]

    assert svc.new_subscriptions(2026, 7) == 1
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_novas_subscricoes_is_plan_change_nulo.py -v`
Expected: FAIL — `SubscriptionEvent.is_plan_change.is_(False)` exclui a
linha com `is_plan_change=None`.

- [ ] **Step 3: Aplicar o fix**

Em `new_subscriptions` (`app/services/admin_metrics_service.py:478-489`),
trocar:

```python
        paid = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(PAID_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.is_(False),
            )
            .all()
        )
```

por:

```python
        paid = (
            self.db.query(SubscriptionEvent)
            .filter(
                SubscriptionEvent.event_type.in_(PAID_EVENTS),
                SubscriptionEvent.received_at >= start,
                SubscriptionEvent.received_at <= end,
                SubscriptionEvent.is_plan_change.isnot(True),
            )
            .all()
        )
```

(`.isnot(True)` inclui `False` e `NULL`, exclui só `True` — o oposto de
`.is_(False)`, que exclui `NULL` junto com `True`.)

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_novas_subscricoes_is_plan_change_nulo.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline, nenhuma falha nova. Prestar atenção especial em
testes de `churn_for_month`/`renewal_rate` que também filtram por
`is_plan_change.is_(False)` em queries semelhantes — esta task NÃO muda
essas outras queries (fora do escopo do item 1), só `new_subscriptions`.

- [ ] **Step 6: Commit**

```bash
git add app/services/admin_metrics_service.py tests/unit/test_novas_subscricoes_is_plan_change_nulo.py
git commit -m "fix(admin): new_subscriptions não perde assinante com is_plan_change NULL"
```

---

## Fase 4 — Validação final e fechamento

### Task 14: Expandir o diagnóstico pro checklist completo de 11 aceites

**Depends on:** Tasks 2-13 (todas as anteriores).

**Files:**
- Create: `scripts/validar_rodada7.py` (baseado em `scripts/diagnostico_rodada7.py` da Task 1 + `scripts/validar_rodada6.py` como referência de formato)

**Interfaces:**
- Consumes: `AdminMetricsService`, `PlatformUsageService` — todos os métodos já tocados pelas tasks anteriores.

- [ ] **Step 1: Escrever o script cobrindo os 11 aceites**

```python
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
```

- [ ] **Step 2: Rodar contra o banco de hml**

Run: `PYTHONPATH=$PWD .venv312/bin/python scripts/validar_rodada7.py`

- [ ] **Step 3: Rodar contra produção (mesmo procedimento manual da Rodada 6)**

Trocar temporariamente a `DATABASE_URL` usada pelo `.env` por uma
apontando pro pooler de produção, rodar de novo, reverter a troca depois.
Só leitura — nenhum `INSERT`/`UPDATE`/`DELETE` em nenhum ponto do script.

- [ ] **Step 4: Documentar todos os resultados (hml + produção) no relatório da task**

- [ ] **Step 5: Commit**

```bash
git add scripts/validar_rodada7.py
git commit -m "test(admin): script de validação dos 11 aceites da rodada 7"
```

### Task 15: Regressão manual, CHANGELOG e fechamento

**Depends on:** Task 14.

**Files:**
- Modify: `CHANGELOG.md` (raiz do monorepo)
- Modify: `marketdash-backend/CLAUDE.md` (se algum aprendizado desta rodada merecer virar regra, como a fragilidade do `cast(...,Date)` pro fuso — seguir o padrão das entradas já existentes em "Critical Rules")

- [ ] **Step 1: Regressão manual (aceite 11)**

Run: `npm run dev` (frontend) + `uvicorn app.main:app --reload --port 8081` (backend). Conferir manualmente que nada quebrou:
- Sync Shopee (manual, um período curto)
- Sync Meta/Facebook (manual)
- OAuth (login/logout do fluxo principal, Supabase)
- Pausar/ativar campanha
- Edição de orçamento de campanha

- [ ] **Step 2: Teste da 1 hora (aceite 4)**

Se ainda não foi feito na Task 7: logar no app, usar normalmente por 1h
sem deslogar, confirmar no banco que `UserLogin` ganhou exatamente 1
linha nova para esse usuário nesse período. Reportar o resultado.

- [ ] **Step 3: Checagem visual dos itens 6, 7, 8 (aceites)**

Confirmar visualmente (screenshot ou descrição) que: label do MRR não
corta; Dashboard em grid 2×2; paginação de Clientes com "Mostrando X–Y de
N" no rodapé.

- [ ] **Step 4: Atualizar `CHANGELOG.md`**

Adicionar uma seção "Rodada 7" seguindo o formato já usado no arquivo
(ver seção da Rodada 6/Campanhas mais recente, no topo), cobrindo os 10
itens do brief: quais viraram fix, qual (item 1) foi investigado e
confirmado correto (ou corrigido, dependendo do resultado da Task 13), e
o resultado do teste da 1 hora.

- [ ] **Step 5: Commit final**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog da rodada 7 do painel admin"
```
