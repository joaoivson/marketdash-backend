# Painel Admin — Rodada 6 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir as cobranças duplicadas do painel admin trocando a reconciliação heurística por uma chave determinística (`order_ref`), redefinir MRR/ativos/upgrade/renovação, e completar os ajustes de gráfico e lista pedidos na rodada 6.

**Architecture:** Nenhuma cobrança é armazenada — tudo é derivado de `subscription_events` em tempo de query. A duplicação existe porque `extract_paid_charges_union()` monta cobranças a partir do array cumulativo `charges.completed[]` (chaveado pelo `order_id` interno da Kiwify) **e** o import histórico injetou um array sintético chaveado pelo `order_ref`. A mesma cobrança entra por duas chaves e soma duas vezes. A correção extrai um novo módulo `app/services/charges.py` onde a cobrança passa a ser **o próprio evento pago, chaveado por `order_ref`**; o array vira só verificação. Como nada é persistido calculado, a limpeza do estado atual acontece sozinha na primeira query após o deploy — **não há DELETE a rodar**.

**Tech Stack:** FastAPI + SQLAlchemy + PostgreSQL (Supabase) no backend; React + Vite + Recharts + shadcn/ui no frontend; pytest.

## Global Constraints

- Comandos de teste do backend: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest <caminho> -v` — o `pytest tests/ -v` do CLAUDE.md **não funciona** (venv 3.9 quebra na coleção).
- Frontend: `cd marketdash-frontend && npx tsc --noEmit && npm run lint` antes de cada commit que toca `.tsx`/`.ts`.
- Camadas do backend: `routes → services → repositories → models`. Não pular camadas.
- Não modificar componentes em `marketdash-frontend/src/components/ui/` — estender via wrapper.
- **Item 9 é leitura apenas.** Nenhuma migration, alteração de schema ou escrita nas tabelas `custom_links`, `custom_link_events`, `capture_sites`, `page_events`. Só `SELECT` agregado.
- Nenhuma migration de banco é necessária em nenhuma task deste plano.
- Cores fixas: azul `#318CE9`, verde `#2BD699`, vermelho `#ef4444`, tooltip `#0d1726`, hover band `rgba(49,140,233,.08)`.
- Números de aceite (base 11/08/2026): Ativos = **30** · MRR líquido = **R$1.411,98** · ARPU = **R$47,07** · Novas ago = **14** · Churn ago = **4** · Renovação ago = **50%** · Letícia **R$181,50** · Alexandre **R$181,50** · Bruna Alves **R$135,70** · Bruna Cabral **R$42,35** · Mariana **R$94,99**.
- Commits seguem `tipo(escopo): descrição curta` (ex.: `fix(admin): cobrança única por order_ref`).

---

## Contexto que o implementador precisa saber

**Por que dobrou.** `scripts/import_kiwify_historico.py:368-379` cria, para cada linha de `import_cobrancas.csv`, um `charges_completed` sintético cujo `order_id` é na verdade o **`order_ref`** (ex.: `"QTqDAVh"`). O webhook real da Kiwify traz no array entradas com o `order_id` interno (UUID). `extract_paid_charges_union()` (`app/services/admin_metrics_service.py:232-295`) deduplica por esse `order_id` — duas chaves diferentes para a mesma cobrança, soma dobrada. Bruna Cabral e Mariana mostram valores diferentes porque o import trouxe `my_commission` real e o array trouxe o `amount` pré-afiliado.

**Por que não precisa DELETE.** Não existe tabela de cobranças. `list_clients` → `_paid_total_for_events` e `revenue_for_month` derivam tudo de `subscription_events` a cada request. Trocar a função de extração corrige total pago, faturamento, MRR e gráficos de uma vez.

**Chave nova.** `order_ref` está no topo do payload do webhook, já é extraído em `subscription_event_recorder.py:114` e já é gravado na coluna `subscription_events.order_ref`. O import também grava (`import_kiwify_historico.py:383`). É a chave comum determinística.

**Marcador de origem.** Eventos do import têm `dedupe_key` começando com `"import:"`. É como distinguir import de webhook para a regra de precedência.

## File Structure

**Backend — criar**
- `app/services/charges.py` — extração de cobranças por `order_ref`, verificação do array. Única responsabilidade: "o que é uma cobrança e quanto vale".
- `scripts/backfill_plan_changes.py` — one-off: re-aplica a regra de upgrade bidirecional no histórico já gravado.
- `scripts/validar_rodada6.py` — imprime todos os números de aceite.
- `tests/unit/test_charges_por_order_ref.py`
- `tests/unit/test_mrr_cancelado_sai.py`
- `tests/unit/test_plan_change_qualquer_ordem.py`
- `tests/unit/test_renewal_rate_cancelamento.py`
- `tests/unit/test_platform_usage_links_paginas.py`

**Backend — modificar**
- `app/services/admin_metrics_service.py` — consome `charges.py`; separa "com acesso" de "vai renovar"; MRR histórico respeita cancelamento; renovação por vencimento; série novas×canceladas.
- `app/services/subscription_event_recorder.py` — plan change em qualquer ordem; alerta de array desconhecido.
- `app/services/platform_usage_service.py` — `acessos` por dia; links/páginas por usuária.
- `app/api/v1/routes/admin_panel.py` — filtro de status multi-valor.
- `tests/unit/test_charges_union.py` — deletado, substituído por `test_charges_por_order_ref.py`.

**Frontend — criar**
- `src/features/admin/components/chart-defaults.tsx` — padrão visual único dos gráficos (item 7).

**Frontend — modificar**
- `src/features/admin/pages/AdminDashboard.tsx` — sublinha de ativos removida; padrão visual; gráfico novas×canceladas.
- `src/features/admin/components/PlatformUsageTab.tsx` — padrão visual; card sem "contas no total"; colunas Links/Páginas.
- `src/features/admin/pages/AdminClients.tsx` — colisão de célula, filtro padrão, ordenação.
- `src/features/admin/pages/AdminClientDetail.tsx` — bloco Links/Páginas.
- `src/features/admin/lib/format.ts` — data curta.
- `src/services/admin-panel.service.ts` — tipos novos.

---

## Fase 1 — Cobrança única por `order_ref` (item 1, URGENTE)

### Task 1: Módulo `charges.py` — cobrança identificada por `order_ref`

**Files:**
- Create: `marketdash-backend/app/services/charges.py`
- Test: `marketdash-backend/tests/unit/test_charges_por_order_ref.py`

**Interfaces:**
- Consumes: nada (módulo novo, base da fase).
- Produces:
  - `PAID_EVENT_TYPES: set[str]`
  - `charge_key(ev) -> str | None`
  - `is_import_event(ev) -> bool`
  - `extract_paid_charges(events: list) -> list[dict]` — cada dict tem as chaves `order_ref`, `net_cents`, `gross_cents`, `fee_cents`, `paid_at` (datetime aware ou None), `plan` (str), `frequency` (str), `from_import` (bool).
  - `total_paid_net(events) -> int`
  - `unknown_array_charges(events, known_ids: set[str]) -> list[dict]`

- [ ] **Step 1: Escrever o teste que falha**

Criar `marketdash-backend/tests/unit/test_charges_por_order_ref.py`:

```python
"""Rodada 6, item 1: cobrança única identificada por order_ref.

O array charges.completed deixa de ser fonte de cobrança — só o próprio
evento pago conta, e import + webhook da MESMA cobrança colapsam num só.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

from app.services.charges import (
    charge_key,
    extract_paid_charges,
    is_import_event,
    total_paid_net,
    unknown_array_charges,
)


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        amount_net_cents=None,
        amount_gross_cents=None,
        fee_cents=None,
        approved_date=None,
        received_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_import_e_webhook_da_mesma_cobranca_viram_uma_so():
    importado = _ev(
        id=1,
        order_id="QTqDAVh",
        order_ref="QTqDAVh",
        dedupe_key="import:cobranca:QTqDAVh",
        amount_net_cents=18150,
        amount_gross_cents=19700,
        approved_date=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
    )
    webhook = _ev(
        id=2,
        order_id="c4456ec2-uuid",
        order_ref="QTqDAVh",
        dedupe_key="wh:c4456ec2",
        amount_net_cents=18150,
        amount_gross_cents=19700,
        approved_date=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
    )
    cobrancas = extract_paid_charges([importado, webhook])
    assert len(cobrancas) == 1
    assert total_paid_net([importado, webhook]) == 18150


def test_valor_do_webhook_prevalece_sobre_o_do_import():
    importado = _ev(
        id=1, order_ref="R1", dedupe_key="import:cobranca:R1", amount_net_cents=4235
    )
    webhook = _ev(id=2, order_ref="R1", dedupe_key="wh:2", amount_net_cents=6050)
    assert total_paid_net([importado, webhook]) == 6050


def test_array_charges_completed_nao_gera_cobranca():
    """Bruna Cabral: import 42,35 + array 60,50 = 102,85 no painel. Só 42,35 é real."""
    importado = _ev(
        id=1,
        order_id="A1",
        order_ref="A1",
        dedupe_key="import:cobranca:A1",
        amount_net_cents=4235,
    )
    outro_webhook = _ev(
        id=2,
        order_ref="B2",
        dedupe_key="wh:2",
        amount_net_cents=0,
        charges_completed=[
            {"order_id": "uuid-antigo", "status": "paid", "amount": 6050}
        ],
    )
    assert total_paid_net([importado, outro_webhook]) == 4235


def test_order_approved_e_subscription_renewed_da_mesma_cobranca_contam_uma_vez():
    a = _ev(id=1, event_type="order_approved", order_ref="X", amount_net_cents=6050)
    b = _ev(id=2, event_type="subscription_renewed", order_ref="X", amount_net_cents=6050)
    assert total_paid_net([a, b]) == 6050


def test_evento_nao_pago_nao_vira_cobranca():
    cancelado = _ev(event_type="subscription_canceled", order_ref="X", amount_net_cents=6050)
    assert extract_paid_charges([cancelado]) == []


def test_evento_sem_order_ref_cai_no_order_id():
    legado = _ev(id=7, order_id="legacy-1", order_ref=None, amount_net_cents=4500)
    assert charge_key(legado) == "oid:legacy-1"
    assert total_paid_net([legado]) == 4500


def test_paid_at_usa_approved_date_e_cai_pro_received_at():
    com_approved = _ev(
        order_ref="A",
        amount_net_cents=100,
        approved_date=datetime(2026, 4, 28, tzinfo=timezone.utc),
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert extract_paid_charges([com_approved])[0]["paid_at"].month == 4

    sem_approved = _ev(
        order_ref="B",
        amount_net_cents=100,
        approved_date=None,
        received_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
    )
    assert extract_paid_charges([sem_approved])[0]["paid_at"].month == 7


def test_bruto_cai_pra_tabela_do_plano_quando_ausente():
    ev = _ev(
        order_ref="A",
        amount_net_cents=13570,
        amount_gross_cents=None,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="trimestral",
    )
    cobranca = extract_paid_charges([ev])[0]
    assert cobranca["gross_cents"] >= cobranca["net_cents"]


def test_is_import_event():
    assert is_import_event(_ev(dedupe_key="import:cobranca:X")) is True
    assert is_import_event(_ev(dedupe_key="abc|order_approved|")) is False


def test_array_com_cobranca_desconhecida_e_reportada():
    ev = _ev(
        order_ref="A",
        amount_net_cents=100,
        charges_completed=[
            {"order_id": "conhecida", "status": "paid", "amount": 100},
            {"order_id": "fantasma", "status": "paid", "amount": 999},
            {"order_id": "nao-paga", "status": "waiting_payment", "amount": 50},
        ],
    )
    desconhecidas = unknown_array_charges([ev], known_ids={"conhecida"})
    assert [c["order_id"] for c in desconhecidas] == ["fantasma"]
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_charges_por_order_ref.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'app.services.charges'`

- [ ] **Step 3: Escrever `app/services/charges.py`**

```python
"""Cobranças da Kiwify — uma cobrança, uma chave: `order_ref`.

Rodada 6, item 1. Antes, cobrança era reconstruída do array cumulativo
`Subscription.charges.completed[]`, deduplicado pelo `order_id` interno da
Kiwify. O import histórico (scripts/import_kiwify_historico.py) injetou um
array sintético cujo `order_id` era na verdade o `order_ref` do export — duas
chaves para a mesma cobrança, faturamento e total pago dobrados.

Agora a cobrança É o evento pago, chaveado pelo `order_ref` que existe tanto no
topo do payload do webhook quanto no "ID da venda" do export usado no import.
O array não gera mais cobrança: vira só verificação (ver `unknown_array_charges`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.core.plans import list_price_cents

PAID_EVENT_TYPES = {
    "order_approved",
    "subscription_renewed",
    "compra_aprovada",
}


def is_import_event(ev) -> bool:
    """Evento veio do import histórico (não de um webhook próprio da cobrança)."""
    return (getattr(ev, "dedupe_key", None) or "").startswith("import:")


def charge_key(ev) -> Optional[str]:
    """Identidade da cobrança. `order_ref` é a chave comum entre import e webhook.

    Cai no `order_id` para eventos legados gravados antes de `order_ref` existir;
    em último caso usa o id do próprio evento, que nunca colide (e portanto nunca
    deduplica — comportamento correto quando não há como saber que é a mesma
    cobrança).
    """
    ref = (getattr(ev, "order_ref", None) or "").strip()
    if ref:
        return f"ref:{ref}"
    oid = (getattr(ev, "order_id", None) or "").strip()
    if oid:
        return f"oid:{oid}"
    ident = getattr(ev, "id", None)
    return f"ev:{ident}" if ident is not None else None


def _normalize_plan_label(name: Optional[str], plan_id: Optional[str] = None) -> str:
    blob = f"{name or ''} {plan_id or ''}".lower()
    if "max" in blob:
        return "max"
    if "pro" in blob:
        return "pro"
    return "essencial"


def _better(candidato, atual) -> bool:
    """Qual dos dois eventos representa melhor a mesma cobrança.

    Ordem: webhook ganha do import (o `my_commission` do webhook é a fonte
    autoritativa do líquido); depois quem tem líquido preenchido; depois o mais
    antigo por `received_at`, só para o resultado ser estável entre queries.
    """
    if is_import_event(candidato) != is_import_event(atual):
        return is_import_event(atual)  # atual é import, candidato não → troca

    tem_net_cand = getattr(candidato, "amount_net_cents", None) is not None
    tem_net_atual = getattr(atual, "amount_net_cents", None) is not None
    if tem_net_cand != tem_net_atual:
        return tem_net_cand

    r_cand = getattr(candidato, "received_at", None)
    r_atual = getattr(atual, "received_at", None)
    if r_cand is not None and r_atual is not None:
        return r_cand < r_atual
    return False


def _as_charge(ev) -> Dict[str, Any]:
    net = getattr(ev, "amount_net_cents", None) or 0
    gross = getattr(ev, "amount_gross_cents", None) or 0
    fee = getattr(ev, "fee_cents", None)

    plan = _normalize_plan_label(
        getattr(ev, "plan_name", None), getattr(ev, "plan_id", None)
    )
    frequency = getattr(ev, "plan_frequency", None) or "monthly"

    if not gross:
        tabela = list_price_cents(plan, frequency)
        gross = tabela if tabela is not None else net

    return {
        "order_ref": (getattr(ev, "order_ref", None) or getattr(ev, "order_id", None) or ""),
        "net_cents": net,
        "gross_cents": gross,
        "fee_cents": fee if fee is not None else max(gross - net, 0),
        "paid_at": getattr(ev, "approved_date", None) or getattr(ev, "received_at", None),
        "plan": plan,
        "frequency": frequency,
        "from_import": is_import_event(ev),
    }


def extract_paid_charges(events) -> List[Dict[str, Any]]:
    """Cobranças pagas distintas da lista de eventos, uma por `order_ref`."""
    melhor: Dict[str, Any] = {}
    for ev in events:
        if (getattr(ev, "event_type", None) or "").lower() not in PAID_EVENT_TYPES:
            continue
        chave = charge_key(ev)
        if not chave:
            continue
        atual = melhor.get(chave)
        if atual is None or _better(ev, atual):
            melhor[chave] = ev
    return [_as_charge(ev) for ev in melhor.values()]


def total_paid_net(events) -> int:
    return sum(c["net_cents"] for c in extract_paid_charges(events))


def _charges_completed_for_event(ev) -> list:
    """O array cumulativo do webhook, do campo dedicado ou do payload cru."""
    completed = getattr(ev, "charges_completed", None)
    if isinstance(completed, list) and completed:
        return completed
    raw = getattr(ev, "raw_payload", None)
    if not isinstance(raw, dict):
        return []
    for key in ("Subscription", "subscription"):
        sub = raw.get(key)
        if not isinstance(sub, dict):
            continue
        charges = sub.get("charges")
        if isinstance(charges, dict) and isinstance(charges.get("completed"), list):
            return charges["completed"]
    return []


def unknown_array_charges(events, known_ids: Set[str]) -> List[Dict[str, Any]]:
    """Entradas `paid` do array que não correspondem a nenhuma cobrança conhecida.

    Verificação, não fonte: a resposta correta a um achado aqui é investigar um
    webhook perdido, nunca inserir a cobrança automaticamente.
    """
    desconhecidas: List[Dict[str, Any]] = []
    vistos: Set[str] = set()
    for ev in events:
        for ch in _charges_completed_for_event(ev):
            if not isinstance(ch, dict):
                continue
            if (ch.get("status") or "").lower() != "paid":
                continue
            oid = ch.get("order_id")
            if not oid:
                continue
            oid = str(oid)
            if oid in known_ids or oid in vistos:
                continue
            vistos.add(oid)
            desconhecidas.append(ch)
    return desconhecidas
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_charges_por_order_ref.py -v`
Expected: PASS — 10 passed

- [ ] **Step 5: Commit**

```bash
cd marketdash-backend
git add app/services/charges.py tests/unit/test_charges_por_order_ref.py
git commit -m "feat(admin): cobrança identificada por order_ref em charges.py"
```

---

### Task 2: `admin_metrics_service` passa a usar `charges.py`

**Files:**
- Modify: `marketdash-backend/app/services/admin_metrics_service.py:176-361` e `:509-535`
- Delete: `marketdash-backend/tests/unit/test_charges_union.py`
- Modify: `marketdash-backend/tests/unit/test_admin_metrics_service.py:9-21` (imports)

**Interfaces:**
- Consumes: `extract_paid_charges`, `total_paid_net` de `app.services.charges` (Task 1).
- Produces: `revenue_from_charges_for_month(events, year, month) -> {"net": int, "gross": int}` e `build_coverage_periods(events) -> Dict[str, List[dict]]` com a mesma assinatura de antes, agora alimentados por `order_ref`.

- [ ] **Step 1: Deletar o teste da união por array**

O arquivo inteiro testa o comportamento que estamos removendo (o array como fonte de cobrança). A cobertura equivalente vive agora em `test_charges_por_order_ref.py`.

```bash
cd marketdash-backend
git rm tests/unit/test_charges_union.py
```

- [ ] **Step 2: Escrever o teste que falha**

Acrescentar ao final de `marketdash-backend/tests/unit/test_admin_metrics_service.py`:

```python
def test_faturamento_do_mes_nao_dobra_com_import_e_webhook():
    """Rodada 6 item 1: mesma cobrança vinda do import e do webhook conta uma vez."""
    from app.services.admin_metrics_service import revenue_from_charges_for_month

    importado = SimpleNamespace(
        id=1,
        event_type="order_approved",
        order_id="QTqDAVh",
        order_ref="QTqDAVh",
        dedupe_key="import:cobranca:QTqDAVh",
        amount_net_cents=18150,
        amount_gross_cents=19700,
        fee_cents=1550,
        approved_date=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        received_at=datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc),
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        charges_completed=None,
        raw_payload={},
    )
    webhook = SimpleNamespace(
        **{**importado.__dict__, "id": 2, "order_id": "uuid", "dedupe_key": "wh:2"}
    )
    rev = revenue_from_charges_for_month([importado, webhook], 2026, 8)
    assert rev["net"] == 18150
```

- [ ] **Step 3: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_admin_metrics_service.py::test_faturamento_do_mes_nao_dobra_com_import_e_webhook -v`
Expected: FAIL — `assert 36300 == 18150`

- [ ] **Step 4: Reescrever o bloco de cobranças em `admin_metrics_service.py`**

Em `app/services/admin_metrics_service.py`, **substituir integralmente** o trecho das linhas 176 a 361 (das funções `_dedupe_by_charge` até `_legacy_paid_in_month`, inclusive) por:

```python
def _dedupe_by_charge(events: List[SubscriptionEvent]) -> List[SubscriptionEvent]:
    """Um evento por cobrança (usado só por estornos, que não passam por charges.py)."""
    seen: set = set()
    result: List[SubscriptionEvent] = []
    for ev in events:
        if ev.order_id:
            if ev.order_id in seen:
                continue
            seen.add(ev.order_id)
        result.append(ev)
    return result


def total_paid_net_from_charges(events) -> int:
    return total_paid_net(events)


def revenue_from_charges_for_month(events, year: int, month: int) -> dict:
    net = gross = 0
    for c in extract_paid_charges(events):
        dt = c.get("paid_at")
        if not dt:
            continue
        # Mês civil BRT, não UTC — cobrança de fim de mês perto da meia-noite
        # (ex.: 21h BRT = 00h UTC do dia seguinte) senão cai no mês errado.
        d = _brt_date(dt) if isinstance(dt, datetime) else dt
        if d.year == year and d.month == month:
            net += c["net_cents"]
            gross += c["gross_cents"]
    return {"net": net, "gross": gross}


def _paid_total_for_events(events, paid_events=None) -> int:
    """Total pago líquido do assinante — cobranças distintas por order_ref."""
    return total_paid_net(events)


def _fees_from_charges_for_month(events, year: int, month: int) -> int:
    fees = 0
    for c in extract_paid_charges(events):
        dt = c.get("paid_at")
        if not dt:
            continue
        d = _brt_date(dt) if isinstance(dt, datetime) else dt
        if d.year == year and d.month == month:
            fees += c.get("fee_cents") or 0
    return fees
```

No topo do arquivo, logo depois de `from app.core.plans import list_price_cents` (linha 21), acrescentar:

```python
from app.services.charges import extract_paid_charges, total_paid_net
```

- [ ] **Step 5: Ajustar `build_coverage_periods` e `revenue_for_month`**

Em `build_coverage_periods`, trocar a linha `for c in extract_paid_charges_union(evs):` por:

```python
        for c in extract_paid_charges(evs):
```

Em `revenue_for_month` (por volta da linha 509), substituir o corpo até o cálculo de `net` por:

```python
    def revenue_for_month(self, year: int, month: int) -> Dict[str, int]:
        start, end = _month_bounds(year, month)
        all_events = self._all_events()
        # Sem caminho legado: toda cobrança é um evento pago com order_ref.
        charges_rev = revenue_from_charges_for_month(all_events, year, month)
        gross = charges_rev["gross"]
        net = charges_rev["net"]
```

O restante do método (bloco de `refunds` em diante) fica inalterado.

Em `series_12m`, trocar `for c in extract_paid_charges_union(all_events):` por `for c in extract_paid_charges(all_events):`.

- [ ] **Step 6: Corrigir os imports do teste existente**

Em `marketdash-backend/tests/unit/test_admin_metrics_service.py`, o bloco de import das linhas 9-21 referencia `revenue_from_charges_for_month`, que continua existindo — nenhuma mudança necessária. Rodar a suíte inteira para pegar qualquer outro consumidor de `extract_paid_charges_union`:

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -v -k "charge or metrics or mrr or import_kiwify"`
Expected: todos PASS. Se algum teste importar `extract_paid_charges_union`, trocar por `extract_paid_charges` (mesma semântica de retorno, chave diferente).

- [ ] **Step 7: Rodar a suíte unitária inteira**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: PASS (sem falhas novas)

- [ ] **Step 8: Commit**

```bash
cd marketdash-backend
git add app/services/admin_metrics_service.py tests/unit/test_admin_metrics_service.py
git commit -m "fix(admin): fim da reconciliação por array — cobrança vem de order_ref"
```

---

### Task 3: Array vira verificação — alerta de webhook perdido

**Files:**
- Modify: `marketdash-backend/app/services/subscription_event_recorder.py:274-294`
- Test: `marketdash-backend/tests/unit/test_subscription_event_recorder.py`

**Interfaces:**
- Consumes: `unknown_array_charges` de `app.services.charges` (Task 1).
- Produces: `alertar_cobrancas_desconhecidas(db, ev) -> list[str]` em `subscription_event_recorder` — lista de `order_id` desconhecidos, já logada como warning.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar ao final de `marketdash-backend/tests/unit/test_subscription_event_recorder.py`:

```python
def test_array_com_cobranca_desconhecida_alerta_e_nao_insere(caplog):
    """Rodada 6 item 1: o array não insere cobrança — só denuncia webhook perdido."""
    import logging
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from app.services.subscription_event_recorder import alertar_cobrancas_desconhecidas

    ev = SimpleNamespace(
        subscription_id="sub-1",
        customer_cpf=None,
        customer_email="a@b.com",
        charges_completed=[
            {"order_id": "conhecida", "status": "paid", "amount": 100},
            {"order_id": "fantasma", "status": "paid", "amount": 999},
        ],
        raw_payload={},
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        ("conhecida", "REF1"),
    ]
    with caplog.at_level(logging.WARNING):
        desconhecidas = alertar_cobrancas_desconhecidas(db, ev)

    assert desconhecidas == ["fantasma"]
    assert "possível webhook perdido" in caplog.text
    db.add.assert_not_called()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_subscription_event_recorder.py::test_array_com_cobranca_desconhecida_alerta_e_nao_insere -v`
Expected: FAIL com `ImportError: cannot import name 'alertar_cobrancas_desconhecidas'`

- [ ] **Step 3: Implementar em `subscription_event_recorder.py`**

No topo do arquivo, depois de `from app.models.user import User` (linha 12), acrescentar:

```python
from app.services.charges import unknown_array_charges
```

Antes de `def record_subscription_event(` (linha 203), acrescentar:

```python
def alertar_cobrancas_desconhecidas(db: Session, ev) -> list:
    """O array `charges.completed` não insere nada — só denuncia buraco no histórico.

    Rodada 6, item 1. Antes, cada entrada do array virava cobrança, o que
    duplicava tudo que também veio pelo import. Agora, se o array cita uma
    cobrança que não temos como evento, é sinal de webhook perdido — alguém
    precisa olhar, mas nada entra automático.
    """
    subscription_id = getattr(ev, "subscription_id", None)
    email = getattr(ev, "customer_email", None)
    if subscription_id:
        filtro = SubscriptionEvent.subscription_id == subscription_id
    elif email:
        filtro = SubscriptionEvent.customer_email == email
    else:
        return []

    conhecidos = set()
    for order_id, order_ref in (
        db.query(SubscriptionEvent.order_id, SubscriptionEvent.order_ref)
        .filter(filtro)
        .all()
    ):
        if order_id:
            conhecidos.add(str(order_id))
        if order_ref:
            conhecidos.add(str(order_ref))

    desconhecidas = unknown_array_charges([ev], conhecidos)
    for ch in desconhecidas:
        logger.warning(
            "possível webhook perdido: cobrança %s (assinatura %s / %s) aparece no "
            "array charges.completed mas não existe como evento — verificar na Kiwify",
            ch.get("order_id"),
            subscription_id,
            email,
        )
    return [str(ch.get("order_id")) for ch in desconhecidas]
```

Dentro de `record_subscription_event`, logo depois de `db.flush()` (linha 275), acrescentar:

```python
        # Verificação (não inserção): o array cita cobranças que não conhecemos?
        alertar_cobrancas_desconhecidas(db, row)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_subscription_event_recorder.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd marketdash-backend
git add app/services/subscription_event_recorder.py tests/unit/test_subscription_event_recorder.py
git commit -m "feat(admin): array charges.completed vira verificação, não fonte"
```

---

## Fase 2 — MRR, ativos, upgrade e renovação (itens 2, 3, 4)

### Task 4: Cancelada sai do MRR e do card de ativos

**Files:**
- Modify: `marketdash-backend/app/services/admin_metrics_service.py` — acrescentar `_is_canceled`, `cancel_instants`, `renewing_subscribers`; alterar `mrr_cents`, `mrr_at`, `series_12m`, `dashboard`
- Test: `marketdash-backend/tests/unit/test_mrr_cancelado_sai.py`

**Interfaces:**
- Consumes: `build_coverage_periods` (Task 2).
- Produces:
  - `_is_canceled(ev) -> bool`
  - `cancel_instants(events) -> Dict[str, List[datetime]]` — instantes de cancelamento real por assinante (exclui `is_plan_change` e ajuste do produtor).
  - `AdminMetricsService.renewing_subscribers(as_of=None) -> List[SubscriptionEvent]`
  - `AdminMetricsService.mrr_at(momento, periodos=None, cancelamentos=None)`
  - `active_subscribers()` **mantém** a semântica atual (quem tem acesso) — é o denominador do Uso.

- [ ] **Step 1: Escrever o teste que falha**

Criar `marketdash-backend/tests/unit/test_mrr_cancelado_sai.py`:

```python
"""Rodada 6, item 2: cancelada sai do MRR no mês do cancelamento.

O acesso continua (produto), a receita esperada não (negócio). Caso Daniel:
cancelou em julho com acesso até outubro — churn em julho e MRR até outubro
era incoerente.
"""
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import (
    AdminMetricsService,
    _is_canceled,
    cancel_instants,
)


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        order_id=None,
        order_ref=None,
        dedupe_key="wh:1",
        subscription_id="sub-1",
        customer_email="daniel@example.com",
        customer_cpf=None,
        received_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=datetime(2026, 10, 10, tzinfo=timezone.utc),
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


def test_is_canceled_reconhece_evento_e_status():
    assert _is_canceled(_ev(event_type="subscription_canceled")) is True
    assert _is_canceled(_ev(subscription_status="canceled")) is True
    assert _is_canceled(_ev()) is False


def test_cancel_instants_ignora_plan_change_e_ajuste_do_produtor():
    real = _ev(
        id=1,
        event_type="subscription_canceled",
        canceled_at=datetime(2026, 7, 20, tzinfo=timezone.utc),
    )
    upgrade = _ev(id=2, event_type="subscription_canceled", is_plan_change=True)
    produtor = _ev(
        id=3, event_type="subscription_canceled", cancel_reason="Cancelado pelo produtor"
    )
    instantes = cancel_instants([real, upgrade, produtor])
    todos = [dt for lista in instantes.values() for dt in lista]
    assert todos == [datetime(2026, 7, 20, tzinfo=timezone.utc)]


def test_renewing_exclui_cancelada_com_acesso():
    svc = AdminMetricsService(MagicMock())
    cancelada = _ev(
        id=1,
        event_type="subscription_canceled",
        subscription_status="canceled",
        has_access=True,
        access_until=datetime(2026, 10, 10, tzinfo=timezone.utc),
    )
    ativa = _ev(id=2, subscription_id="sub-2", customer_email="ana@example.com")
    svc._all_events = lambda: [cancelada, ativa]

    hoje = date(2026, 8, 11)
    com_acesso = svc.active_subscribers(as_of=hoje)
    renovando = svc.renewing_subscribers(as_of=hoje)

    assert len(com_acesso) == 2  # denominador do Uso mantém a cancelada
    assert [e.subscription_id for e in renovando] == ["sub-2"]


def test_mrr_at_zera_no_mes_do_cancelamento():
    svc = AdminMetricsService(MagicMock())
    periodos = {
        "sub:daniel": [
            {
                "inicio": datetime(2026, 7, 10, tzinfo=timezone.utc),
                "fim": datetime(2026, 10, 10, tzinfo=timezone.utc),
                "net_cents": 18150,
                "gross_cents": 19700,
                "divisor": 3,
            }
        ]
    }
    cancelamentos = {"sub:daniel": [datetime(2026, 7, 20, tzinfo=timezone.utc)]}

    antes = svc.mrr_at(datetime(2026, 7, 15, tzinfo=timezone.utc), periodos, cancelamentos)
    depois = svc.mrr_at(datetime(2026, 7, 31, tzinfo=timezone.utc), periodos, cancelamentos)
    agosto = svc.mrr_at(datetime(2026, 8, 31, tzinfo=timezone.utc), periodos, cancelamentos)

    assert antes["net"] == 6050  # 18150 / 3
    assert depois["net"] == 0
    assert agosto["net"] == 0


def test_cancelamento_anterior_ao_periodo_nao_derruba_reassinatura():
    """Cancelou em maio, voltou em julho: o cancelamento velho não mata o MRR novo."""
    svc = AdminMetricsService(MagicMock())
    periodos = {
        "cpf:1": [
            {
                "inicio": datetime(2026, 7, 1, tzinfo=timezone.utc),
                "fim": datetime(2026, 8, 1, tzinfo=timezone.utc),
                "net_cents": 6050,
                "gross_cents": 6700,
                "divisor": 1,
            }
        ]
    }
    cancelamentos = {"cpf:1": [datetime(2026, 5, 20, tzinfo=timezone.utc)]}
    assert svc.mrr_at(datetime(2026, 7, 20, tzinfo=timezone.utc), periodos, cancelamentos)["net"] == 6050
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_mrr_cancelado_sai.py -v`
Expected: FAIL com `ImportError: cannot import name '_is_canceled'`

- [ ] **Step 3: Acrescentar `_is_canceled` e `cancel_instants`**

Em `app/services/admin_metrics_service.py`, logo depois de `_is_active_now` (por volta da linha 419), acrescentar:

```python
def _is_canceled(ev) -> bool:
    """Assinatura cancelada — por tipo de evento ou por status da assinatura."""
    if (getattr(ev, "event_type", None) or "").lower() == "subscription_canceled":
        return True
    return (getattr(ev, "subscription_status", None) or "").lower() in ("canceled", "cancelled")


def cancel_instants(events) -> Dict[str, List[datetime]]:
    """Instantes de cancelamento REAL por assinante.

    Rodada 6, item 2. Base pro MRR histórico: uma assinatura só conta no mês M
    se cobria o fim de M E não estava cancelada até lá. Upgrade/downgrade
    (`is_plan_change`) e ajuste do produtor não são saída de cliente.
    """
    por_assinante: Dict[str, List[datetime]] = defaultdict(list)
    for ev in events:
        if (ev.event_type or "").lower() not in CANCEL_EVENTS:
            continue
        if getattr(ev, "is_plan_change", False):
            continue
        motivo = (getattr(ev, "cancel_reason", None) or "").strip().lower()
        if motivo in PRODUTOR_ADJUSTMENT_REASONS:
            continue
        quando = _utc(getattr(ev, "canceled_at", None) or ev.received_at)
        if quando:
            por_assinante[_subscriber_key(ev)].append(quando)
    return dict(por_assinante)
```

- [ ] **Step 4: Acrescentar `renewing_subscribers` e alterar `mrr_cents` / `mrr_at`**

Em `AdminMetricsService`, logo depois de `active_subscribers` (linha 459), acrescentar:

```python
    def renewing_subscribers(self, as_of: Optional[date] = None) -> List[SubscriptionEvent]:
        """Quem VAI RENOVAR: acesso vigente e não cancelada.

        Rodada 6, item 2. É a base do MRR e do card "Assinantes ativos". Quem
        cancelou mas ainda tem acesso continua em `active_subscribers()` — segue
        usando o produto e segue no denominador da aba Uso, mas não é mais
        receita recorrente esperada.
        """
        return [ev for ev in self.active_subscribers(as_of) if not _is_canceled(ev)]
```

Trocar a primeira linha de `mrr_cents` de `actives = actives if actives is not None else self.active_subscribers()` para:

```python
        actives = actives if actives is not None else self.renewing_subscribers()
```

Substituir a assinatura e o corpo de `mrr_at` por:

```python
    def mrr_at(
        self,
        momento: datetime,
        periodos: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        cancelamentos: Optional[Dict[str, List[datetime]]] = None,
    ) -> Dict[str, int]:
        """MRR num instante: vigência paga cobrindo `momento`, descontando cancelamentos.

        Um cancelamento só derruba o período em que ele caiu (ou um anterior) —
        cancelou em maio e reassinou em julho não afeta o MRR de julho.
        """
        if periodos is None:
            periodos = build_coverage_periods(self._all_events())
        if cancelamentos is None:
            cancelamentos = cancel_instants(self._all_events())
        # Mesmo cuidado de mrr_cents: precisão cheia por assinante, arredonda só no total.
        net_frac = gross_frac = 0.0
        for chave, lista in periodos.items():
            cobrindo = None
            for p in lista:
                if p["inicio"] <= momento < p["fim"]:
                    if cobrindo is None or p["inicio"] > cobrindo["inicio"]:
                        cobrindo = p
            if not cobrindo:
                continue
            cancelada = any(
                cobrindo["inicio"] <= c <= momento for c in cancelamentos.get(chave, [])
            )
            if cancelada:
                continue
            net_frac += cobrindo["net_cents"] / cobrindo["divisor"]
            gross_frac += cobrindo["gross_cents"] / cobrindo["divisor"]
        return {"net": int(round(net_frac)), "gross": int(round(gross_frac))}
```

- [ ] **Step 5: Ligar em `series_12m` e `dashboard`**

Em `series_12m`, depois de `periodos = build_coverage_periods(all_events)`, acrescentar:

```python
        cancelamentos = cancel_instants(all_events)
```

e trocar `mrr = self.mrr_at(momento, periodos)` por:

```python
            mrr = self.mrr_at(momento, periodos, cancelamentos)
```

Em `dashboard`, trocar as duas primeiras linhas por:

```python
        actives = self.renewing_subscribers()
```

(o restante do método já usa `actives` para MRR, `plan_breakdown` e ARPU — nada mais muda).

Em `plan_frequency_distribution`, trocar `actives = self.active_subscribers()` por `actives = self.renewing_subscribers()`.

Em `alerts`, **manter** `self.active_subscribers()` — alerta é sobre quem tem acesso.

- [ ] **Step 6: Rodar os testes**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_mrr_cancelado_sai.py tests/unit/test_mrr_historico.py tests/unit/test_subscription_canceled_with_access.py tests/unit/test_platform_usage_base_ativa.py -v`
Expected: PASS. `test_platform_usage_base_ativa` deve continuar passando — o denominador do Uso usa `active_subscribers()`, que não mudou.

- [ ] **Step 7: Commit**

```bash
cd marketdash-backend
git add app/services/admin_metrics_service.py tests/unit/test_mrr_cancelado_sai.py
git commit -m "feat(admin): cancelada sai do MRR e do card de ativos no mês do cancelamento"
```

---

### Task 5: Upgrade em qualquer ordem (caso Ana Ariel)

**Files:**
- Modify: `marketdash-backend/app/services/subscription_event_recorder.py:145-239`
- Create: `marketdash-backend/scripts/backfill_plan_changes.py`
- Test: `marketdash-backend/tests/unit/test_plan_change_qualquer_ordem.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces: `encontrar_par_de_plan_change(db, fields, reference_time=None) -> SubscriptionEvent | None` — o evento parceiro (cancelamento ou pagamento) que forma o par de upgrade/continuação; `None` se não houver par.

- [ ] **Step 1: Escrever o teste que falha**

Criar `marketdash-backend/tests/unit/test_plan_change_qualquer_ordem.py`:

```python
"""Rodada 6, item 3: upgrade vale em QUALQUER ordem.

Ana Ariel assinou Essencial 10/08 11:11, Pro 11:19 e só depois a Essencial foi
cancelada. A regra antiga só olhava "cancelou antes, assinou depois" — o par
nunca era detectado, e ela contava como nova assinatura E como churn.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.subscription_event_recorder import encontrar_par_de_plan_change

CPF = "12345678900"
BASE = datetime(2026, 8, 10, 11, 0, tzinfo=timezone.utc)


def _ev(**kwargs):
    defaults = dict(
        id=1,
        event_type="order_approved",
        customer_cpf=CPF,
        plan_name="Essencial",
        plan_frequency="monthly",
        received_at=BASE,
        is_plan_change=False,
    )
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _db_com(eventos):
    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.limit.return_value.all.return_value = eventos
    return db


def test_cancelamento_depois_da_nova_assinatura_forma_par():
    """Chega o CANCELAMENTO da Essencial; a Pro já entrou 8 minutos antes."""
    pro = _ev(id=2, event_type="order_approved", plan_name="Pro", received_at=BASE + timedelta(minutes=19))
    fields = {
        "event_type": "subscription_canceled",
        "customer_cpf": CPF,
        "plan_name": "Essencial",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([pro]), fields, reference_time=BASE + timedelta(minutes=20)
    )
    assert par is pro


def test_nova_assinatura_depois_do_cancelamento_continua_funcionando():
    """Ordem antiga: cancelou, depois reassinou plano diferente."""
    cancelamento = _ev(id=3, event_type="subscription_canceled", plan_name="Essencial")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(days=3)
    )
    assert par is cancelamento


def test_mesmo_plano_em_ate_um_dia_e_continuacao():
    cancelamento = _ev(id=4, event_type="subscription_canceled", plan_name="Pro")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(hours=2)
    )
    assert par is cancelamento


def test_mesmo_plano_depois_de_uma_semana_nao_e_par():
    cancelamento = _ev(id=5, event_type="subscription_canceled", plan_name="Pro")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(days=7)
    )
    assert par is None


def test_plano_diferente_depois_de_40_dias_nao_e_par():
    cancelamento = _ev(id=6, event_type="subscription_canceled", plan_name="Essencial")
    fields = {
        "event_type": "order_approved",
        "customer_cpf": CPF,
        "plan_name": "Pro",
        "plan_frequency": "monthly",
    }
    par = encontrar_par_de_plan_change(
        _db_com([cancelamento]), fields, reference_time=BASE + timedelta(days=40)
    )
    assert par is None


def test_sem_cpf_nao_forma_par():
    fields = {"event_type": "order_approved", "customer_cpf": None, "plan_name": "Pro"}
    assert encontrar_par_de_plan_change(_db_com([]), fields) is None
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_plan_change_qualquer_ordem.py -v`
Expected: FAIL com `ImportError: cannot import name 'encontrar_par_de_plan_change'`

- [ ] **Step 3: Substituir `_mark_plan_change_if_needed`**

Em `app/services/subscription_event_recorder.py`, **substituir integralmente** `_mark_plan_change_if_needed` (linhas 145-200) por:

```python
PAID_LIKE_EVENTS = ("order_approved", "subscription_renewed", "compra_aprovada")


def encontrar_par_de_plan_change(
    db: Session, fields: Dict[str, Any], reference_time: Optional[datetime] = None
):
    """Par de upgrade/continuação do evento que está chegando — em QUALQUER ordem.

    Rodada 6, item 3. A regra antiga só olhava "cancelou antes, assinou depois".
    Ana Ariel assinou a Pro 8 minutos ANTES da Essencial ser cancelada, e o par
    nunca era detectado: ela contava como nova assinatura E como churn no mesmo
    mês. Agora a janela é de ±30 dias e vale nos dois sentidos.

    - Mesmo plano em ≤1 dia = continuação (trocou forma de pagamento).
    - Plano diferente em ≤30 dias = upgrade/downgrade.

    `reference_time` (default `now()`) permite processar eventos HISTÓRICOS com a
    janela ancorada na data do próprio evento.
    """
    from datetime import timedelta

    cpf = fields.get("customer_cpf")
    if not cpf:
        return None

    evento = (fields.get("event_type") or "").lower()
    if evento in PAID_LIKE_EVENTS:
        procurado = ["subscription_canceled"]
    elif evento == "subscription_canceled":
        procurado = list(PAID_LIKE_EVENTS)
    else:
        return None

    agora = reference_time or datetime.now(timezone.utc)
    janela = timedelta(days=30)
    candidatos = (
        db.query(SubscriptionEvent)
        .filter(
            SubscriptionEvent.customer_cpf == cpf,
            SubscriptionEvent.received_at >= agora - janela,
            SubscriptionEvent.received_at <= agora + janela,
            SubscriptionEvent.event_type.in_(procurado),
        )
        .order_by(SubscriptionEvent.received_at.desc())
        .limit(20)
        .all()
    )

    for ev in candidatos:
        recebido = ev.received_at
        if recebido is not None and recebido.tzinfo is None:
            recebido = recebido.replace(tzinfo=timezone.utc)
        gap = abs(agora - (recebido or agora))
        mesmo_plano = (ev.plan_name or "") == (fields.get("plan_name") or "") and (
            ev.plan_frequency or ""
        ) == (fields.get("plan_frequency") or "")
        if mesmo_plano and gap <= timedelta(days=1):
            return ev
        if not mesmo_plano and gap <= janela:
            return ev
    return None
```

- [ ] **Step 4: Ligar em `record_subscription_event`**

Substituir as linhas 226-239 (do `is_plan_change = _mark_plan_change_if_needed(db, fields)` até o fim do bloco `if recent_cancel:`) por:

```python
        par = encontrar_par_de_plan_change(db, fields)
        is_plan_change = par is not None
        if par is not None and not par.is_plan_change:
            par.is_plan_change = True
```

- [ ] **Step 5: Rodar os testes**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_plan_change_qualquer_ordem.py tests/unit/test_subscription_event_recorder.py -v`
Expected: PASS. Se algum teste antigo referenciar `_mark_plan_change_if_needed`, reescrevê-lo para `encontrar_par_de_plan_change` (retorna o evento em vez de `True`).

- [ ] **Step 6: Escrever o backfill do histórico já gravado**

Criar `marketdash-backend/scripts/backfill_plan_changes.py`:

```python
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
```

- [ ] **Step 7: Commit**

```bash
cd marketdash-backend
git add app/services/subscription_event_recorder.py scripts/backfill_plan_changes.py tests/unit/test_plan_change_qualquer_ordem.py
git commit -m "fix(admin): upgrade detectado em qualquer ordem (caso Ana Ariel)"
```

---

### Task 6: Taxa de renovação — cancelar no vencimento é não-renovação

**Files:**
- Modify: `marketdash-backend/app/services/admin_metrics_service.py:593-631`
- Test: `marketdash-backend/tests/unit/test_renewal_rate_cancelamento.py`

**Interfaces:**
- Consumes: `build_coverage_periods` (Task 2), `extract_paid_charges` (Task 1).
- Produces: `AdminMetricsService.renewal_rate(year, month) -> float | None` — mesma assinatura, base nova.

**Decisão de design a comunicar:** o denominador deixa de sair de `next_payment` e passa a sair do **fim das vigências pagas** (`build_coverage_periods`). Motivo: o import histórico carimbou `next_payment = access_until` em todos os eventos de cada CPF (`import_kiwify_historico.py:237`), então quem cancelou tem `next_payment` no futuro e sumia do denominador — é exatamente por isso que o painel mostra 100%. Vigência é derivada das cobranças e não depende desse campo.

- [ ] **Step 1: Escrever o teste que falha**

Criar `marketdash-backend/tests/unit/test_renewal_rate_cancelamento.py`:

```python
"""Rodada 6, item 4: quem cancela no vencimento conta como NÃO-renovação.

Agosto/2026 até 11/08: Alexandre renovou, Girlene venceu 10/08 e cancelou.
Correto = 50%. O painel mostrava 100% porque a cancelada saía do denominador.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.admin_metrics_service import AdminMetricsService


def _cobranca(order_ref, cpf, quando, net=6050):
    return SimpleNamespace(
        id=abs(hash(order_ref)) % 100000,
        event_type="order_approved",
        order_id=order_ref,
        order_ref=order_ref,
        dedupe_key=f"import:cobranca:{order_ref}",
        subscription_id=None,
        customer_cpf=cpf,
        customer_email=f"{cpf}@example.com",
        received_at=quando,
        approved_date=quando,
        amount_net_cents=net,
        amount_gross_cents=net + 650,
        fee_cents=650,
        plan_name="Pro",
        plan_id="pro",
        plan_frequency="monthly",
        canceled_at=None,
        cancel_reason=None,
        is_plan_change=False,
        subscription_status="active",
        has_access=True,
        access_until=None,
        next_payment=None,
        charges_completed=None,
        raw_payload={},
    )


def test_cancelou_no_vencimento_conta_como_falha():
    # Alexandre: pagou 10/07 (vence 10/08) e pagou de novo 10/08 → renovou.
    alexandre_jul = _cobranca("A-JUL", "111", datetime(2026, 7, 10, tzinfo=timezone.utc))
    alexandre_ago = _cobranca("A-AGO", "111", datetime(2026, 8, 10, tzinfo=timezone.utc))
    # Girlene: pagou 10/07 (vence 10/08) e não pagou mais → não renovou.
    girlene_jul = _cobranca("G-JUL", "222", datetime(2026, 7, 10, tzinfo=timezone.utc))

    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [alexandre_jul, alexandre_ago, girlene_jul]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)

    assert svc.renewal_rate(2026, 8) == 0.5


def test_sem_vencimento_no_mes_retorna_none():
    svc = AdminMetricsService(MagicMock())
    svc._all_events = lambda: [
        _cobranca("X", "111", datetime(2026, 3, 10, tzinfo=timezone.utc))
    ]
    svc._agora = lambda: datetime(2026, 8, 11, 23, 0, tzinfo=timezone.utc)
    assert svc.renewal_rate(2026, 8) is None
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_renewal_rate_cancelamento.py -v`
Expected: FAIL — a implementação atual consulta `self.db` (MagicMock) e não retorna `0.5`

- [ ] **Step 3: Reescrever `renewal_rate`**

Em `app/services/admin_metrics_service.py`, **substituir integralmente** `renewal_rate` (linhas 593-631) por:

```python
    def _agora(self) -> datetime:
        """Ponto de corte do mês corrente — sobrescrito nos testes."""
        return datetime.now(timezone.utc)

    def renewal_rate(self, year: int, month: int) -> Optional[float]:
        """De quem venceu no período: renovou = pagou. Cancelou = não renovou.

        Rodada 6, item 4. O denominador vem do FIM das vigências pagas
        (build_coverage_periods), não de `next_payment`: o import histórico
        carimbou `next_payment = access_until` em todos os eventos do CPF, então
        quem cancelou ficava com vencimento no futuro e sumia do denominador —
        era por isso que o painel marcava 100%.
        """
        start, end = _month_bounds(year, month)
        # Vencimento que ainda não chegou não é renovação falha: no mês corrente
        # o denominador vai só até agora.
        agora = self._agora()
        if end > agora:
            end = agora
        if end < start:
            return None  # mês futuro

        eventos = self._all_events()
        periodos = build_coverage_periods(eventos)

        cobrancas_por_assinante: Dict[str, List[datetime]] = defaultdict(list)
        por_assinante: Dict[str, List] = defaultdict(list)
        for ev in eventos:
            por_assinante[_subscriber_key(ev)].append(ev)
        for chave, evs in por_assinante.items():
            for c in extract_paid_charges(evs):
                quando = _utc(c.get("paid_at"))
                if quando:
                    cobrancas_por_assinante[chave].append(quando)

        # Tolerância: a cobrança de renovação cai em cima do vencimento, mas a
        # Kiwify pode processar com algumas horas de diferença (e o fim da
        # vigência é calculado por soma de meses, não pelo relógio deles).
        tolerancia = timedelta(days=3)

        denominador = 0
        renovaram = 0
        for chave, lista in periodos.items():
            vencimentos = [p["fim"] for p in lista if start <= p["fim"] <= end]
            if not vencimentos:
                continue
            venceu_em = max(vencimentos)
            denominador += 1
            pagou = any(
                (venceu_em - tolerancia) <= quando <= end
                for quando in cobrancas_por_assinante.get(chave, [])
            )
            if pagou:
                renovaram += 1

        if denominador == 0:
            return None
        return round(renovaram / denominador, 4)
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_renewal_rate_cancelamento.py tests/unit/test_admin_metrics_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd marketdash-backend
git add app/services/admin_metrics_service.py tests/unit/test_renewal_rate_cancelamento.py
git commit -m "fix(admin): taxa de renovação conta cancelamento no vencimento como falha"
```

---

### Task 7: Série "Novas × Canceladas por mês" (backend do item 11)

**Files:**
- Modify: `marketdash-backend/app/services/admin_metrics_service.py` — acrescentar `new_vs_canceled_series` e incluir em `series_12m`
- Test: `marketdash-backend/tests/unit/test_admin_metrics_service.py`

**Interfaces:**
- Consumes: `new_subscriptions(year, month)` e `churn_for_month(year, month)` (existentes; já respeitam `is_plan_change` da Task 5 e o ajuste do produtor).
- Produces: `AdminMetricsService.new_vs_canceled_series() -> List[Dict]` com itens `{"month": "YYYY-MM", "novas": int, "canceladas": int}`; exposto em `dashboard()["series"]["new_vs_canceled"]`.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar ao final de `marketdash-backend/tests/unit/test_admin_metrics_service.py`:

```python
def test_serie_novas_x_canceladas_cobre_12_meses_ate_o_atual():
    """Rodada 6 item 11: barras pareadas por mês, últimos 12 meses."""
    from datetime import datetime as dt

    svc = AdminMetricsService(MagicMock())
    svc._agora = lambda: dt(2026, 8, 11, tzinfo=timezone.utc)
    svc.new_subscriptions = lambda y, m: 14 if (y, m) == (2026, 8) else 0
    svc.churn_for_month = lambda y, m: {
        "count": 4 if (y, m) == (2026, 8) else 0,
        "rate": 0.0,
        "start_actives": 0,
    }

    serie = svc.new_vs_canceled_series()
    assert len(serie) == 12
    assert serie[-1] == {"month": "2026-08", "novas": 14, "canceladas": 4}
    assert serie[0]["month"] == "2025-09"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_admin_metrics_service.py::test_serie_novas_x_canceladas_cobre_12_meses_ate_o_atual -v`
Expected: FAIL com `AttributeError: 'AdminMetricsService' object has no attribute 'new_vs_canceled_series'`

- [ ] **Step 3: Implementar**

Em `AdminMetricsService`, logo depois de `series_12m` (por volta da linha 786), acrescentar:

```python
    def new_vs_canceled_series(self) -> List[Dict[str, Any]]:
        """Novas × canceladas nos últimos 12 meses — saldo líquido que move o MRR.

        Rodada 6, item 11. Reaproveita new_subscriptions/churn_for_month, então
        herda as mesmas regras: upgrade não conta dos dois lados (is_plan_change)
        e "Cancelado pelo produtor" não conta churn.
        """
        hoje = self._agora().date()
        serie: List[Dict[str, Any]] = []
        y, m = hoje.year, hoje.month - 11
        while m <= 0:
            m += 12
            y -= 1
        while (y, m) <= (hoje.year, hoje.month):
            serie.append({
                "month": f"{y:04d}-{m:02d}",
                "novas": self.new_subscriptions(y, m),
                "canceladas": self.churn_for_month(y, m)["count"],
            })
            m += 1
            if m > 12:
                m, y = 1, y + 1
        return serie
```

Em `series_12m`, trocar o `return` final por:

```python
        return {
            "mrr": mrr_series,
            "revenue": rev_series,
            "new_vs_canceled": self.new_vs_canceled_series(),
        }
```

- [ ] **Step 4: Rodar o teste e confirmar que passa**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_admin_metrics_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd marketdash-backend
git add app/services/admin_metrics_service.py tests/unit/test_admin_metrics_service.py
git commit -m "feat(admin): série novas x canceladas por mês"
```

---

## Fase 3 — Uso da plataforma (itens 5 e 9, backend)

### Task 8: `acessos` por dia + colunas Links e Páginas

**Files:**
- Modify: `marketdash-backend/app/services/platform_usage_service.py:184-233` e `:263-272`
- Modify: `marketdash-backend/app/services/admin_metrics_service.py:1080-1089` (bloco `usage` da ficha)
- Test: `marketdash-backend/tests/unit/test_platform_usage_links_paginas.py`

**Interfaces:**
- Consumes: nada de tasks anteriores.
- Produces:
  - `PlatformUsageService.usuarias_por_dia(periodo)` passa a devolver `{"date": str, "usuarias": int, "acessos": int}`.
  - `PlatformUsageService.uso_de_links_e_paginas(periodo, user_ids) -> Dict[int, Dict[str, int]]` com as chaves `links_em_uso`, `links_criados`, `paginas_em_uso`, `paginas_criadas`.
  - `atividade_por_usuaria(periodo)` inclui essas 4 chaves em cada linha.
  - `client_detail(user_id)["usage"]` inclui as mesmas 4 chaves (janela fixa de 30 dias).

- [ ] **Step 1: Escrever o teste que falha**

Criar `marketdash-backend/tests/unit/test_platform_usage_links_paginas.py`:

```python
"""Rodada 6, itens 5 e 9: barras de acessos por dia e colunas Links/Páginas.

Links/Páginas medem operação construída na plataforma: quem tem link rodando em
anúncio não cancela sem dor. LEITURA APENAS — nenhuma escrita nas tabelas do
produto.

Banco SQLite em memória, mesmo padrão de tests/unit/test_platform_usage_base_ativa.py
— o comportamento das queries precisa ser exercido de verdade.
"""
import inspect
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.capture_site import CaptureSite
from app.models.custom_link import CustomLink
from app.models.custom_link_event import CustomLinkEvent
from app.models.page_event import PageEvent
from app.models.user import User
from app.models.user_login import UserLogin
from app.services.platform_usage_service import PlatformUsageService

AGORA = datetime.now(timezone.utc)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for modelo in (User, UserLogin, CustomLink, CustomLinkEvent, CaptureSite, PageEvent):
        modelo.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _user(db, email):
    u = User(email=email, name=email, hashed_password="x", is_admin=False, is_demo=False)
    db.add(u)
    db.flush()
    return u


def _login(db, user_id, quando):
    db.add(UserLogin(user_id=user_id, logged_at=quando))
    db.flush()


def _link(db, user_id, slug, ativo=True):
    link = CustomLink(
        user_id=user_id,
        name=slug,
        original_url="https://exemplo.com",
        slug=slug,
        is_active=ativo,
    )
    db.add(link)
    db.flush()
    return link


def _clique(db, link, quando):
    db.add(CustomLinkEvent(custom_link_id=link.id, user_id=link.user_id, created_at=quando))
    db.flush()


def _pagina(db, user_id, slug, ativa=True):
    site = CaptureSite(user_id=user_id, slug=slug, is_active=ativa)
    db.add(site)
    db.flush()
    return site


def _visualizacao(db, site, quando, tipo="page_view"):
    db.add(PageEvent(site_id=site.id, event_type=tipo, created_at=quando))
    db.flush()


def test_usuarias_por_dia_conta_hits_e_pessoas_distintas(db):
    """A barra é `acessos` (hits) e a linha é `usuarias` (distintas) — sem as
    duas chaves o gráfico da aba Uso renderiza só a linha."""
    ana = _user(db, "ana@example.com")
    bia = _user(db, "bia@example.com")
    _login(db, ana.id, AGORA - timedelta(hours=1))
    _login(db, ana.id, AGORA - timedelta(hours=2))
    _login(db, bia.id, AGORA - timedelta(hours=3))

    serie = PlatformUsageService(db).usuarias_por_dia("7d")

    assert len(serie) == 1
    assert serie[0]["acessos"] == 3
    assert serie[0]["usuarias"] == 2
    assert "date" in serie[0]


def test_link_ativo_com_clique_no_periodo_esta_em_uso(db):
    """`em uso` = ativo E com clique na janela. Só criar não conta."""
    ana = _user(db, "ana@example.com")
    com_clique = _link(db, ana.id, "rodando")
    _link(db, ana.id, "parado")
    inativo_com_clique = _link(db, ana.id, "desligado", ativo=False)
    _clique(db, com_clique, AGORA - timedelta(days=1))
    _clique(db, inativo_com_clique, AGORA - timedelta(days=1))

    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])

    assert uso[ana.id]["links_criados"] == 3
    assert uso[ana.id]["links_em_uso"] == 1


def test_clique_fora_do_periodo_nao_conta_como_em_uso(db):
    ana = _user(db, "ana@example.com")
    antigo = _link(db, ana.id, "antigo")
    _clique(db, antigo, AGORA - timedelta(days=40))

    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])

    assert uso[ana.id]["links_criados"] == 1
    assert uso[ana.id]["links_em_uso"] == 0


def test_pagina_ativa_com_visualizacao_no_periodo_esta_em_uso(db):
    ana = _user(db, "ana@example.com")
    vista = _pagina(db, ana.id, "oferta")
    _pagina(db, ana.id, "rascunho")
    _visualizacao(db, vista, AGORA - timedelta(days=2))
    # click_group não é visualização de página
    _visualizacao(db, vista, AGORA - timedelta(days=2), tipo="click_group")

    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])

    assert uso[ana.id]["paginas_criadas"] == 2
    assert uso[ana.id]["paginas_em_uso"] == 1


def test_usuaria_sem_nada_recebe_zeros(db):
    ana = _user(db, "ana@example.com")
    uso = PlatformUsageService(db).uso_de_links_e_paginas("7d", [ana.id])
    assert uso[ana.id] == {
        "links_em_uso": 0,
        "links_criados": 0,
        "paginas_em_uso": 0,
        "paginas_criadas": 0,
    }


def test_lista_de_usuarias_vazia_nao_consulta_nada(db):
    assert PlatformUsageService(db).uso_de_links_e_paginas("7d", []) == {}


def test_servico_nao_escreve_nas_tabelas_do_produto():
    """Guarda-corpo estático do item 9 ("leitura apenas").

    Não dá para provar ausência de escrita por comportamento — este teste
    existe para que uma escrita introduzida no futuro quebre o build.
    """
    fonte = inspect.getsource(PlatformUsageService)
    for proibido in (".add(", ".delete(", ".update(", "db.commit("):
        assert proibido not in fonte, f"platform_usage_service não pode usar {proibido}"
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_platform_usage_links_paginas.py -v`
Expected: FAIL — `assert '"acessos"' in fonte` e `AttributeError: uso_de_links_e_paginas`

- [ ] **Step 3: Acrescentar `acessos` em `usuarias_por_dia`**

Em `app/services/platform_usage_service.py`, substituir `usuarias_por_dia` (linhas 184-196) por:

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

- [ ] **Step 4: Acrescentar `uso_de_links_e_paginas`**

No topo do arquivo, junto dos outros imports de modelo (linhas 19-21), acrescentar:

```python
from app.models.capture_site import CaptureSite
from app.models.custom_link import CustomLink
from app.models.custom_link_event import CustomLinkEvent
from app.models.page_event import PageEvent
```

Logo depois de `usuarias_por_dia`, acrescentar:

```python
    def uso_de_links_e_paginas(
        self, periodo: str, user_ids: List[int]
    ) -> Dict[int, Dict[str, int]]:
        """Links e páginas por usuária, no formato `em uso / criados`.

        "Em uso" = ativo (toggle ligado) E com movimento no período — clique pro
        link, visualização pra página. Só criar não conta: o que mede operação é
        estar rodando. LEITURA APENAS nas tabelas do produto.
        """
        vazio = {
            "links_em_uso": 0,
            "links_criados": 0,
            "paginas_em_uso": 0,
            "paginas_criadas": 0,
        }
        if not user_ids:
            return {}
        saida: Dict[int, Dict[str, int]] = {uid: dict(vazio) for uid in user_ids}
        inicio = self._inicio(periodo)

        for uid, total in (
            self.db.query(CustomLink.user_id, func.count(CustomLink.id))
            .filter(CustomLink.user_id.in_(user_ids))
            .group_by(CustomLink.user_id)
            .all()
        ):
            saida.setdefault(uid, dict(vazio))["links_criados"] = total

        links_com_clique = (
            self.db.query(CustomLink.user_id, func.count(func.distinct(CustomLink.id)))
            .join(CustomLinkEvent, CustomLinkEvent.custom_link_id == CustomLink.id)
            .filter(
                CustomLink.user_id.in_(user_ids),
                CustomLink.is_active.is_(True),
                CustomLinkEvent.created_at >= inicio,
            )
            .group_by(CustomLink.user_id)
            .all()
        )
        for uid, total in links_com_clique:
            saida.setdefault(uid, dict(vazio))["links_em_uso"] = total

        for uid, total in (
            self.db.query(CaptureSite.user_id, func.count(CaptureSite.id))
            .filter(CaptureSite.user_id.in_(user_ids))
            .group_by(CaptureSite.user_id)
            .all()
        ):
            saida.setdefault(uid, dict(vazio))["paginas_criadas"] = total

        paginas_vistas = (
            self.db.query(CaptureSite.user_id, func.count(func.distinct(CaptureSite.id)))
            .join(PageEvent, PageEvent.site_id == CaptureSite.id)
            .filter(
                CaptureSite.user_id.in_(user_ids),
                CaptureSite.is_active.is_(True),
                PageEvent.event_type == "page_view",
                PageEvent.created_at >= inicio,
            )
            .group_by(CaptureSite.user_id)
            .all()
        )
        for uid, total in paginas_vistas:
            saida.setdefault(uid, dict(vazio))["paginas_em_uso"] = total

        return saida
```

- [ ] **Step 5: Incluir na tabela de atividade**

Em `atividade_por_usuaria`, substituir o `return` final (linhas 223-233) por:

```python
        ids = [l[0] for l in linhas]
        uso = self.uso_de_links_e_paginas(periodo, ids)
        return [
            {
                "user_id": uid,
                "nome": capitalizar_nome(nomes.get(uid)) or emails.get(uid) or f"#{uid}",
                "email": emails.get(uid),
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

- [ ] **Step 6: Incluir na ficha individual**

Em `app/services/admin_metrics_service.py`, dentro de `client_detail`, substituir o bloco `"usage": {` (linhas 1080-1089) por:

```python
            "usage": {
                "logins_30d": [
                    {"at": l.logged_at.isoformat(), "ip": l.ip} for l in logins
                ],
                "shopee_last_sync": shopee.last_sync_at.isoformat() if shopee and shopee.last_sync_at else None,
                "facebook_last_sync": fb.last_sync_at.isoformat() if fb and getattr(fb, "last_sync_at", None) else None,
                "campaigns_count": int(camps),
                "commission_30d": float(commission or 0),
                "spend_30d": float(spend or 0),
                **_uso_links_paginas_30d(self.db, user_id),
            },
```

E acrescentar, antes da classe `AdminMetricsService` (por volta da linha 439):

```python
def _uso_links_paginas_30d(db: Session, user_id: int) -> Dict[str, int]:
    """Bloco Links/Páginas da ficha individual — mesma regra da aba Uso, 30 dias."""
    from app.services.platform_usage_service import PlatformUsageService

    uso = PlatformUsageService(db).uso_de_links_e_paginas("30d", [user_id])
    return uso.get(user_id, {
        "links_em_uso": 0,
        "links_criados": 0,
        "paginas_em_uso": 0,
        "paginas_criadas": 0,
    })
```

- [ ] **Step 7: Remover `contas_no_total` do payload (item 8)**

Em `platform_usage_service.py`, remover a linha `"contas_no_total": self._contas_no_total(),` do dict de `cards` e apagar o método `_contas_no_total` inteiro (linhas 177-182).

O método tem cobertura: apagar também o teste `test_contas_no_total_exclui_admin_e_demo` em `tests/unit/test_platform_usage_base_ativa.py` (é o último teste do arquivo). Nenhum outro teste toca `_contas_no_total` — confirmar com:

Run: `cd marketdash-backend && grep -rn "contas_no_total" tests/ app/`
Expected: nenhuma ocorrência depois da remoção.

- [ ] **Step 8: Rodar os testes**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_platform_usage_links_paginas.py tests/unit/test_platform_usage_base_ativa.py -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
cd marketdash-backend
git add app/services/platform_usage_service.py app/services/admin_metrics_service.py tests/unit/test_platform_usage_links_paginas.py
git commit -m "feat(admin): acessos por dia + colunas Links/Páginas na aba Uso"
```

---

### Task 9: Filtro de status multi-valor e busca que ignora filtro (backend do item 10)

**Files:**
- Modify: `marketdash-backend/app/services/admin_metrics_service.py:946-971`
- Test: `marketdash-backend/tests/unit/test_admin_metrics_service.py`

**Interfaces:**
- Consumes: nada.
- Produces: `list_clients` aceita `filters["status"]` como string com vírgulas (`"ativo,atrasado,cancelado_com_acesso"`); quando `filters["q"]` está preenchido, o filtro de status é ignorado.

- [ ] **Step 1: Escrever o teste que falha**

Acrescentar ao final de `marketdash-backend/tests/unit/test_admin_metrics_service.py`:

```python
def test_status_filter_aceita_lista_e_busca_ignora_filtro():
    """Rodada 6 item 10: padrão sem Inativo, mas buscar "Débora" acha a inativa."""
    from app.services.admin_metrics_service import _status_permitido

    padrao = "ativo,atrasado,cancelado_com_acesso"
    assert _status_permitido("ativo", padrao, busca=None) is True
    assert _status_permitido("inativo", padrao, busca=None) is False
    # com busca ativa, o filtro de status não elimina ninguém
    assert _status_permitido("inativo", padrao, busca="debora") is True
    # sem filtro, tudo passa
    assert _status_permitido("inativo", None, busca=None) is True
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_admin_metrics_service.py::test_status_filter_aceita_lista_e_busca_ignora_filtro -v`
Expected: FAIL com `ImportError: cannot import name '_status_permitido'`

- [ ] **Step 3: Implementar**

Em `app/services/admin_metrics_service.py`, logo depois de `_client_display_status` (linha 437), acrescentar:

```python
def _status_permitido(status: str, filtro: Optional[str], busca: Optional[str]) -> bool:
    """O status do cliente passa pelo filtro da lista?

    Rodada 6, item 10. `filtro` aceita vários status separados por vírgula — a
    lista abre em "Ativo + Atrasado + Cancelado c/ acesso". E busca vence filtro:
    digitar o nome de uma inativa tem que encontrá-la, senão parece que ela sumiu
    do sistema.
    """
    if busca:
        return True
    if not filtro:
        return True
    permitidos = {s.strip() for s in filtro.split(",") if s.strip()}
    return not permitidos or status in permitidos
```

Em `list_clients`, substituir as linhas do filtro de status:

```python
            if filters.get("status") and filters["status"] != status:
                continue
```

por:

```python
            if not _status_permitido(status, filters.get("status"), q):
                continue
```

- [ ] **Step 4: Rodar os testes**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_admin_metrics_service.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd marketdash-backend
git add app/services/admin_metrics_service.py tests/unit/test_admin_metrics_service.py
git commit -m "feat(admin): filtro de status multi-valor e busca que ignora o filtro"
```

---

## Fase 4 — Frontend

### Task 10: Padrão visual único dos gráficos (item 7)

**Files:**
- Create: `marketdash-frontend/src/features/admin/components/chart-defaults.tsx`
- Modify: `marketdash-frontend/src/features/admin/pages/AdminDashboard.tsx:1-12, 158-200`
- Modify: `marketdash-frontend/src/features/admin/components/PlatformUsageTab.tsx:4-13, 170-219`

**Interfaces:**
- Consumes: `CHART_COLORS` e `AdminChartTooltip` de `AdminChartTooltip.tsx` (já existentes).
- Produces (de `chart-defaults.tsx`):
  - `HOVER_BAND = "rgba(49,140,233,.08)"`
  - `BAR_CURSOR: { fill: string }` e `LINE_CURSOR: { stroke: string; strokeWidth: number }`
  - `AXIS_PROPS: { tickLine: false; axisLine: false; fontSize: 11 }`
  - `LABEL_STYLE: React.CSSProperties`
  - `formatMilhar(valor: number) -> string` — sem centavos, separador de milhar pt-BR
  - `ValueLabelList(props: { dataKey: string; formatter?: (v: number) => string; position?: "top" | "right" })` — componente que devolve um `<LabelList>` já estilizado

- [ ] **Step 1: Criar o módulo do padrão visual**

Criar `marketdash-frontend/src/features/admin/components/chart-defaults.tsx`:

```tsx
import { LabelList } from "recharts";

/**
 * Padrão visual único dos gráficos do painel admin (rodada 6, item 7).
 *
 * Sem gridlines de fundo, hover band escuro no lugar do destaque branco do
 * recharts, e valor exposto sem precisar de hover — o tooltip continua
 * existindo para o valor completo.
 */

/** Faixa de hover escura — o destaque branco/cinza padrão morre em fundo escuro. */
export const HOVER_BAND = "rgba(49,140,233,.08)";

export const BAR_CURSOR = { fill: HOVER_BAND } as const;
export const LINE_CURSOR = { stroke: HOVER_BAND, strokeWidth: 32 } as const;

/** Eixos sem linha e sem tick — só os números. Nenhum CartesianGrid nos gráficos. */
export const AXIS_PROPS = {
  tickLine: false,
  axisLine: false,
  fontSize: 11,
} as const;

/** Valor exposto: mono pequeno, cor clara discreta. */
export const LABEL_STYLE: React.CSSProperties = {
  fontFamily: '"JetBrains Mono", ui-monospace, SFMono-Regular, Menlo, monospace',
  fontSize: 10,
  fill: "#94a3b8",
};

/** Sem centavos: 1650.4 → "1.650". Zero vira string vazia (não polui o gráfico). */
export function formatMilhar(valor: number): string {
  if (!valor) return "";
  return Math.round(valor).toLocaleString("pt-BR");
}

export function ValueLabelList({
  dataKey,
  formatter = formatMilhar,
  position = "top",
}: {
  dataKey: string;
  formatter?: (valor: number) => string;
  position?: "top" | "right";
}) {
  return (
    <LabelList
      dataKey={dataKey}
      position={position}
      style={LABEL_STYLE}
      formatter={(v: number) => formatter(Number(v))}
    />
  );
}
```

- [ ] **Step 2: Aplicar no AdminDashboard**

Em `marketdash-frontend/src/features/admin/pages/AdminDashboard.tsx`:

Trocar o import do recharts (linhas 2-12) por:

```tsx
import {
  Bar,
  BarChart,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
```

Acrescentar depois do import de `AdminChartTooltip` (linha 24):

```tsx
import {
  AXIS_PROPS,
  BAR_CURSOR,
  LINE_CURSOR,
  ValueLabelList,
} from "@/features/admin/components/chart-defaults";
```

Substituir o `<LineChart>` do MRR (linhas 165-171) por:

```tsx
              <LineChart data={mrrSeries}>
                <XAxis dataKey="month" {...AXIS_PROPS} />
                <YAxis {...AXIS_PROPS} />
                <Tooltip
                  cursor={LINE_CURSOR}
                  content={<AdminChartTooltip valueFormatter={(v) => centsToBRL(Math.round(v * 100))} />}
                />
                <Line type="monotone" dataKey="líquido" stroke={CHART_COLORS.blue} strokeWidth={2} dot={{ r: 3 }}>
                  <ValueLabelList dataKey="líquido" />
                </Line>
              </LineChart>
```

Substituir o `<BarChart>` do faturamento (linhas 186-192) por:

```tsx
              <BarChart data={revSeries}>
                <XAxis dataKey="month" {...AXIS_PROPS} />
                <YAxis {...AXIS_PROPS} />
                <Tooltip
                  cursor={BAR_CURSOR}
                  content={<AdminChartTooltip valueFormatter={(v) => centsToBRL(Math.round(v * 100))} />}
                />
                <Bar dataKey="líquido" fill={CHART_COLORS.blue} radius={[4, 4, 0, 0]}>
                  <ValueLabelList dataKey="líquido" />
                </Bar>
              </BarChart>
```

- [ ] **Step 3: Aplicar no PlatformUsageTab**

Em `marketdash-frontend/src/features/admin/components/PlatformUsageTab.tsx`, remover `CartesianGrid` do import do recharts (linha 6) e acrescentar depois do import de `AdminChartTooltip` (linha 24):

```tsx
import { AXIS_PROPS, BAR_CURSOR } from "@/features/admin/components/chart-defaults";
```

Substituir o corpo do `<ComposedChart>` (linhas 171-218) por:

```tsx
                  <ComposedChart data={data.usuarias_por_dia}>
                    <XAxis dataKey="date" tickFormatter={dataCurta} {...AXIS_PROPS} fontSize={12} />
                    <YAxis yAxisId="acessos" allowDecimals={false} {...AXIS_PROPS} fontSize={12} />
                    <YAxis
                      yAxisId="usuarias"
                      orientation="right"
                      allowDecimals={false}
                      {...AXIS_PROPS}
                      fontSize={12}
                    />
                    <Tooltip
                      cursor={BAR_CURSOR}
                      content={
                        <AdminChartTooltip
                          labelFormatter={(v) => new Date(String(v)).toLocaleDateString("pt-BR")}
                        />
                      }
                    />
                    <Bar
                      yAxisId="acessos"
                      dataKey="acessos"
                      name="Acessos"
                      fill={CHART_COLORS.blue}
                      radius={[4, 4, 0, 0]}
                    />
                    <Line
                      yAxisId="usuarias"
                      type="monotone"
                      dataKey="usuarias"
                      name="Usuárias"
                      stroke={CHART_COLORS.green}
                      strokeWidth={2}
                      dot={{ r: 3 }}
                    />
                  </ComposedChart>
```

Nota: sem `LabelList` aqui — as duas séries sobrepostas com valor exposto ficam ilegíveis. O tooltip conjunto ("10/08 — 132 acessos · 17 usuárias") já entrega o número, e é o que o item 5 pede.

- [ ] **Step 4: Verificar tipos e lint**

Run: `cd marketdash-frontend && npx tsc --noEmit && npm run lint`
Expected: sem erros

- [ ] **Step 5: Commit**

```bash
cd marketdash-frontend
git add src/features/admin/components/chart-defaults.tsx src/features/admin/pages/AdminDashboard.tsx src/features/admin/components/PlatformUsageTab.tsx
git commit -m "style(admin): padrão visual único dos gráficos — sem grid, hover escuro, valores expostos"
```

---

### Task 11: Gráfico Novas × Canceladas e sublinha de ativos removida (itens 2 e 11, frontend)

**Files:**
- Modify: `marketdash-frontend/src/services/admin-panel.service.ts` — tipo `AdminDashboard.series`
- Modify: `marketdash-frontend/src/features/admin/pages/AdminDashboard.tsx:133-138, 200-224`

**Interfaces:**
- Consumes: `dashboard()["series"]["new_vs_canceled"]` (Task 7); `chart-defaults.tsx` (Task 10).
- Produces: nada consumido adiante.

- [ ] **Step 1: Acrescentar o tipo no service**

Em `marketdash-frontend/src/services/admin-panel.service.ts`, dentro do tipo `AdminDashboard`, no campo `series`, acrescentar a chave:

```ts
    new_vs_canceled: { month: string; novas: number; canceladas: number }[];
```

- [ ] **Step 2: Remover a sublinha do card de ativos**

Em `AdminDashboard.tsx`, substituir o `MetricCard` de "Assinantes ativos" (linhas 133-138) por:

```tsx
        <MetricCard title="Assinantes ativos" badge="hoje" value={String(data.active_count)} />
```

E apagar a variável `planBits` (linhas 92-94), que fica sem uso.

- [ ] **Step 3: Acrescentar o gráfico**

Antes do `<Card className="lg:col-span-2">` de "Plano × periodicidade" (linha 201), inserir:

```tsx
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">Novas × canceladas por mês</CardTitle>
          </CardHeader>
          <CardContent className="h-64">
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={data.series?.new_vs_canceled || []}>
                <XAxis dataKey="month" {...AXIS_PROPS} />
                <YAxis allowDecimals={false} {...AXIS_PROPS} />
                <Tooltip cursor={BAR_CURSOR} content={<AdminChartTooltip />} />
                <Bar dataKey="novas" name="Novas" fill={CHART_COLORS.green} radius={[4, 4, 0, 0]}>
                  <ValueLabelList dataKey="novas" />
                </Bar>
                <Bar dataKey="canceladas" name="Canceladas" fill={CHART_COLORS.red} radius={[4, 4, 0, 0]}>
                  <ValueLabelList dataKey="canceladas" />
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </CardContent>
        </Card>
```

- [ ] **Step 4: Verificar tipos e lint**

Run: `cd marketdash-frontend && npx tsc --noEmit && npm run lint`
Expected: sem erros

- [ ] **Step 5: Commit**

```bash
cd marketdash-frontend
git add src/services/admin-panel.service.ts src/features/admin/pages/AdminDashboard.tsx
git commit -m "feat(admin): gráfico novas x canceladas e card de ativos sem sublinha"
```

---

### Task 12: Aba Uso — card sem "contas no total" e colunas Links/Páginas (itens 8 e 9, frontend)

**Files:**
- Modify: `marketdash-frontend/src/services/admin-panel.service.ts:254-276`
- Modify: `marketdash-frontend/src/features/admin/components/PlatformUsageTab.tsx:129-158, 224-263`
- Modify: `marketdash-frontend/src/features/admin/pages/AdminClientDetail.tsx:185-215`

**Interfaces:**
- Consumes: `atividade[].links_em_uso|links_criados|paginas_em_uso|paginas_criadas` e `usage.links_*|paginas_*` (Task 8).
- Produces: nada consumido adiante.

- [ ] **Step 1: Atualizar os tipos**

Em `marketdash-frontend/src/services/admin-panel.service.ts`, no tipo `PlatformUsage`:

- remover a linha `contas_no_total: number;` de `cards`
- substituir o bloco `atividade` por:

```ts
  atividade: {
    user_id: number;
    nome: string;
    email: string | null;
    acessos: number;
    dias_ativos: number;
    links_em_uso: number;
    links_criados: number;
    paginas_em_uso: number;
    paginas_criadas: number;
    ultimo_acesso: string | null;
  }[];
```

- [ ] **Step 2: Remover a linha "contas no total"**

Em `PlatformUsageTab.tsx`, substituir o bloco `<div>` que envolve o `CardNumero` de "Usuárias ativas" (linhas 135-150) por:

```tsx
            <CardNumero
              titulo="Usuárias ativas"
              valor={data.cards.usuarias_ativas}
              subtitulo={
                data.cards.taxa_uso == null
                  ? "sem base ativa"
                  : `${data.cards.usuarias_ativas} de ${data.cards.base_ativa} ativas · ${(
                      data.cards.taxa_uso * 100
                    ).toFixed(0)}%`
              }
            />
```

- [ ] **Step 3: Acrescentar as colunas Links e Páginas**

Em `PlatformUsageTab.tsx`, no `<TableHeader>` (linhas 231-236), inserir entre "Dias ativos" e "Último acesso":

```tsx
                    <TableHead className="text-right">Links</TableHead>
                    <TableHead className="text-right">Páginas</TableHead>
```

No corpo da tabela, inserir entre a célula de `dias_ativos` e a de `ultimo_acesso`:

```tsx
                      <TableCell className="text-right tabular-nums">
                        {u.links_em_uso}/{u.links_criados}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">
                        {u.paginas_em_uso}/{u.paginas_criadas}
                      </TableCell>
```

E trocar o `colSpan={4}` da linha vazia por `colSpan={6}`.

- [ ] **Step 4: Acrescentar o bloco na ficha individual**

Em `marketdash-frontend/src/features/admin/pages/AdminClientDetail.tsx`, logo depois da linha `<p>Campanhas Meta: {data.usage?.campaigns_count ?? 0}</p>`, inserir:

```tsx
            <p>
              Links: {data.usage?.links_em_uso ?? 0} em uso / {data.usage?.links_criados ?? 0} criados
              {" · "}
              Páginas: {data.usage?.paginas_em_uso ?? 0}/{data.usage?.paginas_criadas ?? 0}
            </p>
```

- [ ] **Step 5: Verificar tipos e lint**

Run: `cd marketdash-frontend && npx tsc --noEmit && npm run lint`
Expected: sem erros

- [ ] **Step 6: Commit**

```bash
cd marketdash-frontend
git add src/services/admin-panel.service.ts src/features/admin/components/PlatformUsageTab.tsx src/features/admin/pages/AdminClientDetail.tsx
git commit -m "feat(admin): colunas Links/Páginas na aba Uso e card sem contas no total"
```

---

### Task 13: Lista de Clientes — colisão, filtro padrão e ordenação (itens 6 e 10)

**Files:**
- Modify: `marketdash-frontend/src/features/admin/lib/format.ts:68-76`
- Modify: `marketdash-frontend/src/features/admin/pages/AdminClients.tsx`

**Interfaces:**
- Consumes: filtro de status multi-valor (Task 9).
- Produces: nada consumido adiante.

- [ ] **Step 1: Data curta em `format.ts`**

Substituir `nextChargeLabel` (linhas 68-76) por:

```ts
/**
 * Rótulo da coluna "Próx. cobrança".
 *
 * Cancelado c/ acesso usa ano de 2 dígitos ("até 10/09/26"): o texto completo
 * "Acesso até 10/09/2026" estourava a célula e invadia a coluna Total pago.
 */
export function nextChargeLabel(
  status: string | undefined,
  data: string | null | undefined,
): string {
  if (!data) return "—";
  const d = new Date(data);
  if (status === "cancelado_com_acesso") {
    const curta = d.toLocaleDateString("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      year: "2-digit",
    });
    return `até ${curta}`;
  }
  return d.toLocaleDateString("pt-BR");
}
```

- [ ] **Step 2: Filtro padrão e ordenação em `AdminClients.tsx`**

Depois do import de `format` (linha 7), acrescentar o import de `ArrowUpDown`:

```tsx
import { ArrowUpDown, Download, Loader2, Search } from "lucide-react";
```

(substituindo a linha 3 inteira).

Depois de `function StatusBadge` (linha 71), acrescentar:

```tsx
/** Padrão da lista: quem importa hoje. "Inativo" só quando o filtro for trocado. */
const STATUS_PADRAO = "ativo,atrasado,cancelado_com_acesso";

type SortKey = "name" | "next_payment" | "total_paid_net_cents" | "last_login_at";

const SORT_VALUE: Record<SortKey, (c: AdminClient) => string | number> = {
  name: (c) => (c.name || "").toLowerCase(),
  // Sem próxima cobrança vai pro fim em ordem ascendente (não é urgência).
  next_payment: (c) => c.next_payment || c.access_until || "9999-12-31",
  total_paid_net_cents: (c) => c.total_paid_net_cents || 0,
  last_login_at: (c) => c.last_login_at || "",
};
```

Dentro do componente, trocar a inicialização de `status` (linha 76) por:

```tsx
  const [status, setStatus] = useState(params.get("status") || STATUS_PADRAO);
  // Ordem inicial de urgência: quem vence primeiro no topo.
  const [sortKey, setSortKey] = useState<SortKey>("next_payment");
  const [sortAsc, setSortAsc] = useState(true);
```

Depois do `useEffect` de carga (linha 104), acrescentar:

```tsx
  const sortedRows = useMemo(() => {
    const get = SORT_VALUE[sortKey];
    return [...rows].sort((a, b) => {
      const va = get(a);
      const vb = get(b);
      if (va === vb) return 0;
      const cmp = va < vb ? -1 : 1;
      return sortAsc ? cmp : -cmp;
    });
  }, [rows, sortKey, sortAsc]);

  const toggleSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortAsc((v) => !v);
      return;
    }
    setSortKey(key);
    setSortAsc(true);
  };
```

- [ ] **Step 3: Headers clicáveis e largura das células**

Substituir o `<TableRow>` do header (linhas 185-196) por:

```tsx
                <TableRow>
                  <SortableHead
                    className="w-[15%] whitespace-nowrap"
                    label="Nome"
                    sortKey="name"
                    active={sortKey}
                    asc={sortAsc}
                    onSort={toggleSort}
                  />
                  <TableHead className="w-[16%]">E-mail</TableHead>
                  <TableHead className="w-[8%]">Plano</TableHead>
                  <TableHead className="w-[10%]">Periodicidade</TableHead>
                  <TableHead className="w-[12%]">Status</TableHead>
                  <SortableHead
                    className="w-[12%] whitespace-nowrap"
                    label="Próx. cobrança"
                    sortKey="next_payment"
                    active={sortKey}
                    asc={sortAsc}
                    onSort={toggleSort}
                  />
                  <SortableHead
                    className="w-[9%] whitespace-nowrap"
                    label="Total pago"
                    sortKey="total_paid_net_cents"
                    active={sortKey}
                    asc={sortAsc}
                    onSort={toggleSort}
                  />
                  <SortableHead
                    className="w-[8%] whitespace-nowrap"
                    label="Último acesso"
                    sortKey="last_login_at"
                    active={sortKey}
                    asc={sortAsc}
                    onSort={toggleSort}
                  />
                  <TableHead className="w-[7%]">Integrações</TableHead>
                  <TableHead className="w-[3%]" />
                </TableRow>
```

Trocar `rows.map(` por `sortedRows.map(` e `rows.length === 0` por `sortedRows.length === 0`.

Na célula de "Próx. cobrança" (linha 216), acrescentar contenção:

```tsx
                  <TableCell className="overflow-hidden text-ellipsis whitespace-nowrap text-sm">
```

Acrescentar o componente `SortableHead` antes de `export default function AdminClientsPage()`:

```tsx
function SortableHead({
  label,
  sortKey,
  active,
  asc,
  onSort,
  className,
}: {
  label: string;
  sortKey: SortKey;
  active: SortKey;
  asc: boolean;
  onSort: (key: SortKey) => void;
  className?: string;
}) {
  const ativo = active === sortKey;
  return (
    <TableHead className={className}>
      <button
        type="button"
        onClick={() => onSort(sortKey)}
        className="inline-flex items-center gap-1 transition-colors hover:text-foreground"
      >
        {label}
        <ArrowUpDown
          className={`h-3 w-3 ${ativo ? "text-primary" : "text-muted-foreground/50"}`}
          aria-label={ativo ? (asc ? "crescente" : "decrescente") : undefined}
        />
      </button>
    </TableHead>
  );
}
```

- [ ] **Step 4: Opção de filtro padrão no Select**

Substituir o `<SelectContent>` do status (linhas 144-150) por:

```tsx
          <SelectContent>
            <SelectItem value={STATUS_PADRAO}>Ativos (padrão)</SelectItem>
            <SelectItem value="all">Todos status</SelectItem>
            <SelectItem value="ativo">Ativo</SelectItem>
            <SelectItem value="atrasado">Atrasado</SelectItem>
            <SelectItem value="inativo">Inativo</SelectItem>
            <SelectItem value="cancelado_com_acesso">Cancelado c/ acesso</SelectItem>
          </SelectContent>
```

A linha do `<Select>` em si (linha 140) fica **inalterada** — `"all"` continua virando string vazia, que o backend trata como "sem filtro":

```tsx
        <Select value={status || "all"} onValueChange={(v) => setStatus(v === "all" ? "" : v)}>
```

Como `status` agora nasce em `STATUS_PADRAO`, o Select abre em "Ativos (padrão)".

- [ ] **Step 5: Verificar tipos e lint**

Run: `cd marketdash-frontend && npx tsc --noEmit && npm run lint`
Expected: sem erros

- [ ] **Step 6: Verificar no navegador**

Run: `cd marketdash-frontend && npm run dev` e abrir `http://localhost:8080/admin/clientes`
Expected: lista abre sem "Inativo", ordenada por Próx. cobrança ascendente; a célula de data não invade Total pago; clicar em "Total pago" ordena e o segundo clique inverte; digitar "Débora" encontra a inativa.

- [ ] **Step 7: Commit**

```bash
cd marketdash-frontend
git add src/features/admin/lib/format.ts src/features/admin/pages/AdminClients.tsx
git commit -m "fix(admin): colisão de célula, filtro padrão e ordenação na lista de clientes"
```

---

## Fase 5 — Aceite

### Task 14: Script de validação dos números de aceite

**Files:**
- Create: `marketdash-backend/scripts/validar_rodada6.py`

**Interfaces:**
- Consumes: tudo das Tasks 1-9.
- Produces: relatório impresso; sai com código 1 se algum aceite falhar.

- [ ] **Step 1: Escrever o script**

Criar `marketdash-backend/scripts/validar_rodada6.py`:

```python
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
```

- [ ] **Step 2: Rodar a suíte inteira antes de validar contra dados reais**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: PASS, sem falhas

- [ ] **Step 3: Rodar o backfill de plan change em dry-run**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python scripts/backfill_plan_changes.py --dry-run`
Expected: lista o par da Ana Ariel (`order_approved Pro` ↔ `subscription_canceled Essencial`). Conferir a lista antes de rodar sem `--dry-run`.

- [ ] **Step 4: Rodar o backfill de verdade**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python scripts/backfill_plan_changes.py`
Expected: "Commit realizado."

- [ ] **Step 5: Validar os aceites**

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python scripts/validar_rodada6.py`
Expected: exit 0, "Todos os aceites automáticos passaram."

Se algum número não bater, **não ajustar o número esperado** — investigar a regra. Os valores vieram da conferência do Luiz contra os exports da Kiwify.

- [ ] **Step 6: Conferir manualmente o que o script não cobre**

Aceites 2, 3, 8, 9, 10, 11, 12, 13 e 14 são visuais/comportamentais:

| # | O que conferir | Onde |
|---|---|---|
| 2 | Reprocessar um webhook da Letícia → total pago segue R$181,50 | painel `/admin/clientes` |
| 3 | Array com cobrança desconhecida → warning "possível webhook perdido", nada inserido | log da API |
| 8 | Gráfico do Uso com barras de acessos + linha de usuárias, dois eixos | `/admin/uso` |
| 9 | Lista sem colisão, abre sem Inativo e ordenada por Próx. cobrança; busca acha inativa; headers ordenam | `/admin/clientes` |
| 10 | Todos os gráficos sem gridline, hover escuro, valores expostos, tooltip `#0d1726` | `/admin` e `/admin/uso` |
| 11 | Card de usuárias sem a linha de contas totais | `/admin/uso` |
| 12 | Links e Páginas em `em uso/criados`, respondendo ao seletor de período; ficha individual com o mesmo dado | `/admin/uso` e `/admin/clientes/{id}` |
| 13 | Gráfico Novas × Canceladas (ago: 14 verde × 4 vermelho) | `/admin` |
| 14 | Sync Shopee/Meta, OAuth, pausar/ativar campanha e orçamento seguem funcionando | `/dashboard/campanhas` |

Para o 14, rodar também:

Run: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: PASS — nenhum teste de campanha/sync quebrado.

- [ ] **Step 7: Commit**

```bash
cd marketdash-backend
git add scripts/validar_rodada6.py
git commit -m "test(admin): script de validação dos aceites da rodada 6"
```

---

### Task 15: Atualizar CHANGELOG e documentação

**Files:**
- Modify: `CHANGELOG.md` (raiz do monorepo)
- Modify: `marketdash-backend/CLAUDE.md`

- [ ] **Step 1: Entrada no CHANGELOG**

Em `CHANGELOG.md`, no topo, acrescentar:

```markdown
## 2026-08-11 — Painel Admin, rodada 6

### Corrigido
- **Cobranças duplicadas.** A cobrança passa a ser identificada pelo `order_ref`
  (chave comum entre o import histórico e o webhook), não mais reconstruída do
  array cumulativo `charges.completed`. Import e webhook da mesma cobrança
  colapsam num registro só; o valor do webhook (`my_commission`) prevalece.
  Nada é armazenado calculado, então total pago, faturamento e gráficos se
  corrigiram sozinhos — não houve DELETE. Novo módulo `app/services/charges.py`.
- **Array `charges.completed` deixou de inserir cobrança.** Virou verificação:
  cobrança citada no array sem evento correspondente gera warning "possível
  webhook perdido".
- **Upgrade em qualquer ordem.** Cancelamento e nova assinatura de plano
  diferente do mesmo CPF dentro de ±30 dias formam par, independente de qual
  veio primeiro (caso Ana Ariel: assinou a Pro 8 minutos antes de a Essencial
  ser cancelada). Backfill em `scripts/backfill_plan_changes.py`.
- **Taxa de renovação.** Cancelar no vencimento passa a contar como
  não-renovação. O denominador vem do fim das vigências pagas, não de
  `next_payment` — que o import carimbou com `access_until` e por isso tirava as
  canceladas da conta.
- **Colisão de célula** na lista de Clientes (data invadindo Total pago).
- **Gráfico da aba Uso** ganhou a série principal: `usuarias_por_dia` não
  devolvia `acessos`, então as barras não renderizavam.

### Alterado
- **MRR e Assinantes ativos** contam só quem vai renovar: cancelada sai no mês do
  cancelamento, mesmo com acesso vigente. `active_subscribers()` (quem tem
  acesso) segue sendo o denominador da aba Uso; `renewing_subscribers()` é a
  base de MRR/ARPU/LTV.
- **Padrão visual dos gráficos**: sem gridlines, hover band
  `rgba(49,140,233,.08)`, valores expostos sem hover, tooltip `#0d1726`.
  Centralizado em `features/admin/components/chart-defaults.tsx`.
- Lista de Clientes abre em Ativo + Atrasado + Cancelado c/ acesso, ordenada por
  Próx. cobrança ascendente, com headers ordenáveis. A busca ignora o filtro.

### Adicionado
- Gráfico "Novas × canceladas por mês" no Dashboard admin.
- Colunas **Links** e **Páginas** (`em uso / criados`) na tabela de atividade e
  na ficha individual. Leitura apenas — nenhuma alteração nas tabelas do produto.
- `scripts/validar_rodada6.py` com os números de aceite.

### Removido
- Linha "N contas no total" do card de usuárias (e o campo `contas_no_total`
  da API).
```

- [ ] **Step 2: Nota no CLAUDE.md do backend**

Em `marketdash-backend/CLAUDE.md`, na seção "Critical Rules", acrescentar:

```markdown
- **Cobranças Kiwify**: uma cobrança = um evento pago, chaveado por `order_ref`
  (`app/services/charges.py`). O array `Subscription.charges.completed` NÃO é
  fonte de cobrança — só verificação de webhook perdido. Reintroduzir o array
  como fonte volta a duplicar tudo que veio pelo import histórico.
- **MRR ≠ acesso**: `renewing_subscribers()` (não cancelada, acesso vigente) é a
  base de MRR/ARPU/LTV; `active_subscribers()` (tem acesso, mesmo cancelada) é a
  base da aba Uso e dos alertas.
```

- [ ] **Step 3: Commit**

```bash
cd /Users/joaoivson/Desktop/PROJETOS/MarketDash
git add CHANGELOG.md
git -C marketdash-backend add CLAUDE.md
git commit -m "docs: changelog da rodada 6 do painel admin"
git -C marketdash-backend commit -m "docs: regras de cobrança por order_ref e MRR vs acesso"
```

---

## Ordem de execução e dependências

```
Task 1 (charges.py)
  └─ Task 2 (admin_metrics usa charges) ── Task 4 (MRR/ativos) ── Task 7 (série novas×canceladas)
  └─ Task 3 (alerta do array)                      └─ Task 6 (renovação)
Task 5 (plan change) ─────────────────────────────┘   (Task 7 depende de 5 para os números baterem)
Task 8 (uso: acessos + links/páginas)   [independente]
Task 9 (filtro de status)               [independente]
Task 10 (chart-defaults) ── Task 11 (novas×canceladas front, depende de 7)
                         └─ Task 12 (aba Uso front, depende de 8)
Task 13 (lista de clientes, depende de 9)
Task 14 (aceites, depende de tudo)
Task 15 (docs)
```

Tasks 8, 9 e 10 podem rodar em paralelo com a Fase 1/2 — não tocam nos mesmos arquivos das cobranças, exceto o `client_detail` da Task 8 (bloco `usage`), que não conflita com as regiões editadas nas Tasks 2/4/6/9.

## Riscos conhecidos

1. **Os números de aceite dependem dos dados de produção.** As Tasks 1-9 são testadas com fixtures; só a Task 14 confronta com o real. Se `Ativos = 30` não bater, o suspeito nº 1 é a definição de `_is_canceled` num assinante com `subscription_status` fora do esperado — inspecionar com `svc.active_subscribers()` vs `svc.renewing_subscribers()` e comparar as listas.
2. **`renewal_rate` mudou de base.** Se o resultado de agosto não for 50%, imprimir os vencimentos derivados (`build_coverage_periods`) para o CPF da Girlene e do Alexandre e conferir se o `fim` do período cai dentro da janela.
3. **O backfill da Task 5 escreve em produção.** Rodar `--dry-run` primeiro, conferir a lista de pares e só então rodar de verdade. A operação é idempotente.
4. **`extract_paid_charges` só considera eventos pagos com `order_ref` ou `order_id`.** Um evento pago legado sem nenhum dos dois vira uma cobrança própria (chave `ev:<id>`), o que preserva o total mas não deduplica. Se aparecer duplicata residual no aceite, é aqui que se investiga.
