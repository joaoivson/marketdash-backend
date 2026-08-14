# Campanha "Ativa" por Status Real de Veiculação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir campanha arquivada aparecendo como "ativa" no MarketDash, unificar a regra de classificação (ativa/inativa/pausada/arquivada) nos 3 lugares que hoje divergem (contagem, filtro, toggle), e expandir o filtro de status pra refletir os 4 status reais pedidos no documento do time.

**Architecture:** Duas causas raiz, dois fixes independentes: (1) a chamada à Graph API que lista campanhas omite `ARCHIVED`/`DELETED` por padrão — corrigido adicionando filtro explícito; (2) a heurística de 7 dias (zumbi de orçamento esgotado) só era aplicada na contagem do card, não no filtro nem no campo `is_active` que o toggle consome — corrigido extraindo uma função única de classificação usada nos 3 lugares. Nenhuma migration necessária (nenhuma coluna nova).

**Tech Stack:** FastAPI + SQLAlchemy + httpx (Graph API) no backend; React + Vite + shadcn/ui no frontend; pytest.

## Global Constraints

- Comandos de teste do backend: `cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest <caminho> -v` — `pytest` puro não funciona (venv 3.9 quebra na coleção).
- Frontend: `cd marketdash-frontend && npx tsc --noEmit && npm run lint` antes de cada commit que toca `.tsx`/`.ts`.
- **Não mexer em cálculo de markup/imposto/ROAS Real** (fora de escopo, explícito no documento original).
- **Não quebrar a heurística de 7 dias existente** (commit `3d60524`) nem o fix de reativação (`status_active_since`, commits `54768c0`/`e3f3d8c`, migration `050`, já em produção) — ambos continuam necessários; a Task 2 só move a chamada dessa heurística pra dentro da função unificada, não muda a lógica dela.
- `DELETED` fica fora do escopo de sincronização — só `ARCHIVED` volta a ser buscado.
- Não existe campo de "veiculação" separado na API da Meta — todo o trabalho deriva de `effective_status` (já buscado hoje) + a heurística de 7 dias. Ver spec para a investigação completa.
- Existe um arquivo solto e não rastreado `app/services/campaign_service 2.py` (nome com espaço, `git status` mostra `??`) — não é o módulo real (Python não importa arquivo com espaço no nome via `import campaign_service`), não tocar nele, não confundir com `campaign_service.py`.

---

## Contexto que o implementador precisa saber

**Por que a campanha arquivada nunca atualiza.** `facebook_marketing_client.list_campaigns()` chama `GET /{ad_account_id}/campaigns` sem parâmetro de filtro. A documentação oficial da Meta confirma: *"A request with no filters returns only campaigns that were not archived or deleted."* Quando uma campanha é arquivada, ela some da resposta; `facebook_integration_service.sync_user()` só processa (`upsert_campaign`) o que vem na resposta; o último `effective_status` conhecido (tipicamente `ACTIVE`) fica congelado no banco pra sempre. `_is_active()` já trata `ARCHIVED` corretamente (só retorna `True` pra `"ACTIVE"`) — o bug é só a API nunca devolver esse valor pro sync gravar.

**Por que os 3 lugares divergem hoje.** `campaign_service.list_campaigns()` (linhas 274-281) já aplica `_is_active(c) and not c.ad_review_issue and _still_delivering(c, recent_activity_ids)` — mas só pra calcular `active_count_now`/`budget_now` (o card). O filtro de status (linha ~288-291) e o campo `is_active` de cada campanha em `_build_response()` (usado pelo toggle) usam só `_is_active(c)` puro, sem a heurística nem a exclusão de anúncio reprovado.

**Onde `_build_response()` é chamado.** Duas vezes: dentro do loop de `list_campaigns()` (`campaign_service.py`, dentro do `for c in campaigns:` que monta `responses`) e em `app/api/v1/routes/campaigns.py::update_status()` (rota `PATCH /{campaign_id}/status`, depois de mudar o status no Facebook). As duas chamadas precisam ser atualizadas juntas quando a assinatura da função mudar (Task 2).

## File Structure

**Backend — modificar**
- `app/services/facebook_marketing_client.py` — `list_campaigns()` ganha filtro explícito incluindo `ARCHIVED`.
- `app/services/campaign_service.py` — nova função `_is_effectively_active()`, nova `_is_paused()`, `_build_response()` ganha parâmetro `recent_activity_ids`, `STATUS_FILTERS` expandido, filtro de `list_campaigns()` cobre os 4 valores.
- `app/api/v1/routes/campaigns.py` — `list_campaigns()` (rota) atualiza a descrição do Query; `update_status()` calcula `recent_activity_ids` pra passar pro `_build_response()`; opcionalmente rejeita PATCH em campanha arquivada (Task 4).

**Backend — criar**
- `tests/unit/test_facebook_marketing_client_list_campaigns_filtro.py`
- `tests/unit/test_campaign_is_effectively_active.py`
- `tests/unit/test_campaign_status_filter_4_valores.py`
- `tests/unit/test_campaign_patch_status_bloqueia_arquivada.py` (só se a Task 4 for implementada)

**Frontend — modificar**
- `src/shared/types/campaign.ts` — `CampaignStatusFilter` ganha `"inactive"` e `"archived"`.
- `src/features/dashboard/pages/Campanhas.tsx` — toggle bloqueado + tooltip pra arquivada; dropdown do filtro de status com as 4 opções.

---

## Task 1: Sync enxerga campanhas arquivadas

**Files:**
- Modify: `app/services/facebook_marketing_client.py:1-25` (imports), `:244-252` (`list_campaigns`)
- Test: `tests/unit/test_facebook_marketing_client_list_campaigns_filtro.py`

**Interfaces:**
- Produces: `list_campaigns(access_token: str, ad_account_id: str) -> list[dict]` — assinatura não muda, só o parâmetro `filtering` é adicionado à request HTTP.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Rodada campanha-ativa: list_campaigns() passa a pedir ARCHIVED
explicitamente — sem isso, a Graph API omite campanhas arquivadas/
deletadas da resposta por padrão (doc oficial da Meta), e o sync nunca
mais atualiza o status de uma campanha depois que ela é arquivada."""
import json
from unittest.mock import MagicMock, patch

import pytest

from app.services.facebook_marketing_client import list_campaigns


def _fake_response(body: dict):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = body
    return resp


def _async_return(value):
    async def _inner():
        return value

    return _inner()


def _patch_httpx(body: dict, captured: dict):
    """Mocka o AsyncClient do httpx pra devolver `body` sem rede, capturando
    os kwargs da chamada em `captured` pra inspecionar depois."""
    client = MagicMock()

    async def _request(method, url, **kwargs):
        captured["method"] = method
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _fake_response(body)

    client.request = _request
    ctx = MagicMock()
    ctx.__aenter__ = lambda *a, **k: _async_return(client)
    ctx.__aexit__ = lambda *a, **k: _async_return(None)
    return patch("httpx.AsyncClient", return_value=ctx)


@pytest.mark.asyncio
async def test_list_campaigns_filtra_effective_status_incluindo_archived_sem_deleted():
    captured: dict = {}
    with _patch_httpx({"data": []}, captured):
        await list_campaigns("token123", "act_111")

    assert captured["params"] is not None
    assert "filtering" in captured["params"]
    filtros = json.loads(captured["params"]["filtering"])
    assert len(filtros) == 1
    assert filtros[0]["field"] == "effective_status"
    assert filtros[0]["operator"] == "IN"
    valores = filtros[0]["value"]
    assert "ARCHIVED" in valores
    assert "ACTIVE" in valores
    assert "PAUSED" in valores
    assert "DELETED" not in valores  # fora de escopo — campanha deletada, não arquivada
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_facebook_marketing_client_list_campaigns_filtro.py -v`
Expected: FAIL — `assert "filtering" in captured["params"]` (o código atual não manda esse parâmetro).

- [ ] **Step 3: Aplicar o fix**

Adicionar `import json` ao topo de `app/services/facebook_marketing_client.py` (junto dos outros imports, linha ~12-15):

```python
import json
import logging
from typing import Any, Optional
from urllib.parse import urlencode
```

Trocar `list_campaigns()` (linhas 244-252):

```python
async def list_campaigns(access_token: str, ad_account_id: str) -> list[dict]:
    """Lista campanhas de uma ad account (formato 'act_123')."""
    params = {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget",
        "access_token": access_token,
        "limit": 200,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/campaigns"), params)
```

por:

```python
# Sem filtro, a Graph API omite campanhas ARCHIVED/DELETED da resposta por
# padrão ("A request with no filters returns only campaigns that were not
# archived or deleted" — doc oficial da Meta). Isso fazia o sync nunca mais
# tocar numa campanha depois de arquivada, deixando o último effective_status
# conhecido (tipicamente ACTIVE) congelado pra sempre. Inclui ARCHIVED
# explicitamente; DELETED fica de fora de propósito — campanha deletada não
# é "arquivada", é removida de verdade.
_CAMPAIGN_EFFECTIVE_STATUSES = [
    "ACTIVE", "PAUSED", "ARCHIVED", "PENDING_REVIEW", "DISAPPROVED",
    "PREAPPROVED", "PENDING_BILLING_INFO", "CAMPAIGN_PAUSED",
    "ADSET_PAUSED", "IN_PROCESS", "WITH_ISSUES",
]


async def list_campaigns(access_token: str, ad_account_id: str) -> list[dict]:
    """Lista campanhas de uma ad account (formato 'act_123')."""
    filtering = json.dumps([
        {"field": "effective_status", "operator": "IN", "value": _CAMPAIGN_EFFECTIVE_STATUSES}
    ])
    params = {
        "fields": "id,name,status,effective_status,objective,daily_budget,lifetime_budget",
        "filtering": filtering,
        "access_token": access_token,
        "limit": 200,
    }
    return await _get_paginated(_graph_url(f"{ad_account_id}/campaigns"), params)
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_facebook_marketing_client_list_campaigns_filtro.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de falhas pré-existentes (3, em `test_shopee_upsert_additive.py`, não relacionadas). Nenhuma falha NOVA.

- [ ] **Step 6: Commit**

```bash
git add app/services/facebook_marketing_client.py tests/unit/test_facebook_marketing_client_list_campaigns_filtro.py
git commit -m "fix(campanhas): sync passa a enxergar campanhas arquivadas (filtro explícito na Graph API)"
```

## Task 2: Função única de classificação, usada nos 3 lugares

**Depends on:** nenhuma (a lógica funciona com fixtures sintéticas mesmo antes do sync real trazer `ARCHIVED` — Task 1 resolve o dado chegar certo, esta task resolve o dado ser usado certo nos 3 lugares).

**Files:**
- Modify: `app/services/campaign_service.py:127-150` (`_is_active`, `_still_delivering`), `:206-227` (`_build_response`), `:250-291` (`list_campaigns`)
- Modify: `app/api/v1/routes/campaigns.py:1-10` (imports), `:151-169` (`update_status`)
- Test: `tests/unit/test_campaign_is_effectively_active.py`

**Interfaces:**
- Produces: `_is_effectively_active(campaign: Campaign, recent_activity_ids: set) -> bool` — combina `_is_active`, `not ad_review_issue`, `_still_delivering`.
- `_build_response(self, campaign, spend, clicks, impressions, comm, ad_rate=0.0, comm_rate=0.0, reach=0, recent_activity_ids: Optional[set] = None) -> CampaignResponse` — novo parâmetro `recent_activity_ids`, usado internamente pra computar `is_active` via `_is_effectively_active`. Quando `None`, trata como conjunto vazio (nenhuma campanha "recém-reativada" tem carência) — usado só por chamadores que genuinamente não têm esse dado calculado; `list_campaigns()` e `update_status()` sempre passam o valor real.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Campanha-ativa: _is_effectively_active() unifica os 3 critérios que hoje
só a contagem do card aplicava (ativa + sem anúncio reprovado + entrega
real nos últimos 7 dias) — filtro de lista e toggle usavam só
effective_status==ACTIVE puro, deixando zumbi/reprovada aparecerem como
"ativa" em 2 dos 3 lugares."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.campaign import Campaign, CampaignDailyInsight
from app.models.facebook_integration import FacebookIntegration
from app.models.user_settings import UserSettings
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_service import CampaignService, _is_effectively_active

USUARIA = 1
CONTA = "act_111"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Campaign.__table__.create(engine)
    CampaignDailyInsight.__table__.create(engine)
    FacebookIntegration.__table__.create(engine)
    UserSettings.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _campanha(db, **campos):
    padrao = dict(
        user_id=USUARIA, fb_campaign_id="fb", name="Campanha", ad_account_id=CONTA,
        status="ACTIVE", effective_status="ACTIVE",
    )
    padrao.update(campos)
    c = Campaign(**padrao)
    db.add(c)
    db.flush()
    return c


def test_is_effectively_active_true_so_quando_passa_os_3_criterios():
    zumbi = Campaign(
        user_id=USUARIA, fb_campaign_id="zumbi", name="Zumbi", ad_account_id=CONTA,
        status="ACTIVE", effective_status="ACTIVE",
        created_at=datetime.now(timezone.utc) - timedelta(days=40),
        status_active_since=datetime.now(timezone.utc) - timedelta(days=40),
    )
    reprovada = Campaign(
        user_id=USUARIA, fb_campaign_id="reprovada", name="Reprovada", ad_account_id=CONTA,
        status="ACTIVE", effective_status="ACTIVE", ad_review_issue="DISAPPROVED",
    )
    ativa = Campaign(
        user_id=USUARIA, fb_campaign_id="ativa", name="Ativa", ad_account_id=CONTA,
        status="ACTIVE", effective_status="ACTIVE",
    )
    arquivada = Campaign(
        user_id=USUARIA, fb_campaign_id="arquivada", name="Arquivada", ad_account_id=CONTA,
        status="ARCHIVED", effective_status="ARCHIVED",
    )

    recent_activity_ids: set = set()  # nenhuma tem entrega recente registrada
    assert _is_effectively_active(zumbi, recent_activity_ids) is False
    assert _is_effectively_active(reprovada, recent_activity_ids) is False
    assert _is_effectively_active(arquivada, recent_activity_ids) is False
    assert _is_effectively_active(ativa, recent_activity_ids) is True


def test_filtro_active_da_lista_agora_exclui_zumbi_igual_a_contagem(db):
    """Antes desta task, o filtro status=active usava _is_active() puro —
    zumbi aparecia excluído do card mas incluído no filtro "ativa" (o
    insight antigo dela ainda dá "movimento" o bastante pra ela aparecer
    na lista, sem filtro de data). Confirma que os dois agora concordam.
    """
    velha = timedelta(days=40)
    zumbi = _campanha(
        db, fb_campaign_id="zumbi", lifetime_budget=12.0,
        created_at=datetime.now(timezone.utc) - velha,
        status_active_since=datetime.now(timezone.utc) - velha,
    )
    db.add(CampaignDailyInsight(
        user_id=USUARIA, campaign_id=zumbi.id, fb_campaign_id="zumbi",
        date=date.today() - timedelta(days=38), spend=11.8, clicks=1, impressions=10,
    ))
    viva = _campanha(db, fb_campaign_id="viva", daily_budget=10.0)
    db.add(CampaignDailyInsight(
        user_id=USUARIA, campaign_id=viva.id, fb_campaign_id="viva",
        date=date.today() - timedelta(days=1), spend=5.0, clicks=1, impressions=10,
    ))
    import json
    db.add(FacebookIntegration(
        user_id=USUARIA, encrypted_access_token="x", ad_accounts_json=json.dumps([CONTA]),
    ))
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA, status_filter="active")

    assert resp.kpis.active_campaigns_count == 1  # só "viva" no card, como já era
    assert [c.fb_campaign_id for c in resp.campaigns] == ["viva"]  # zumbi some do filtro "ativa" também agora
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_campaign_is_effectively_active.py -v`
Expected: FAIL — `ImportError: cannot import name '_is_effectively_active'` (ainda não existe).

- [ ] **Step 3: Extrair `_is_effectively_active` em `campaign_service.py`**

Depois de `_still_delivering()` (linhas 132-150), adicionar:

```python
def _is_effectively_active(campaign: Campaign, recent_activity_ids: set) -> bool:
    """Mesma regra nos 3 lugares (card, filtro, toggle): ativa pro Facebook,
    sem anúncio reprovado, e com entrega real (não é zumbi de orçamento
    esgotado)."""
    return (
        _is_active(campaign)
        and not campaign.ad_review_issue
        and _still_delivering(campaign, recent_activity_ids)
    )
```

- [ ] **Step 4: Atualizar `_build_response` pra usar a função unificada**

Trocar (linhas ~206-227):

```python
    def _build_response(
        self,
        campaign: Campaign,
        spend: float,
        clicks: int,
        impressions: int,
        comm: dict,
        ad_rate: float = 0.0,
        comm_rate: float = 0.0,
        reach: int = 0,
    ) -> CampaignResponse:
        linked = bool(campaign.sub_id)
        metrics = _compute_metrics(spend, clicks, impressions, comm, ad_rate, comm_rate, reach)
        return CampaignResponse(
            id=campaign.id,
            fb_campaign_id=campaign.fb_campaign_id,
            name=campaign.name,
            status=campaign.status,
            effective_status=campaign.effective_status,
            ad_review_issue=campaign.ad_review_issue,
            objective=campaign.objective,
            daily_budget=campaign.daily_budget,
            sub_id=campaign.sub_id,
            linked=linked,
            is_active=_is_active(campaign),
            health=_health(linked, metrics.spend, metrics.roas),
            metrics=metrics,
        )
```

por:

```python
    def _build_response(
        self,
        campaign: Campaign,
        spend: float,
        clicks: int,
        impressions: int,
        comm: dict,
        ad_rate: float = 0.0,
        comm_rate: float = 0.0,
        reach: int = 0,
        recent_activity_ids: Optional[set] = None,
    ) -> CampaignResponse:
        linked = bool(campaign.sub_id)
        metrics = _compute_metrics(spend, clicks, impressions, comm, ad_rate, comm_rate, reach)
        return CampaignResponse(
            id=campaign.id,
            fb_campaign_id=campaign.fb_campaign_id,
            name=campaign.name,
            status=campaign.status,
            effective_status=campaign.effective_status,
            ad_review_issue=campaign.ad_review_issue,
            objective=campaign.objective,
            daily_budget=campaign.daily_budget,
            sub_id=campaign.sub_id,
            linked=linked,
            is_active=_is_effectively_active(campaign, recent_activity_ids or set()),
            health=_health(linked, metrics.spend, metrics.roas),
            metrics=metrics,
        )
```

- [ ] **Step 5: Atualizar `list_campaigns()` pra reusar a função e passar `recent_activity_ids` adiante**

Trocar o bloco de `active_now`/filtro (linhas ~274-291):

```python
        recent_activity_ids = self.repo.campaign_ids_with_recent_activity(
            user_id, since=date.today() - timedelta(days=RECENT_ACTIVITY_WINDOW_DAYS)
        )
        active_now = [
            c
            for c in campaigns
            if _is_active(c) and not c.ad_review_issue and _still_delivering(c, recent_activity_ids)
        ]
        budget_now = round(sum(c.daily_budget or 0.0 for c in active_now), 2)
        active_count_now = len(active_now)

        if search:
            term = search.strip().lower()
            campaigns = [c for c in campaigns if term in (c.name or "").lower()]
        if status_filter == "active":
            campaigns = [c for c in campaigns if _is_active(c)]
        elif status_filter == "paused":
            campaigns = [c for c in campaigns if not _is_active(c)]
```

por:

```python
        recent_activity_ids = self.repo.campaign_ids_with_recent_activity(
            user_id, since=date.today() - timedelta(days=RECENT_ACTIVITY_WINDOW_DAYS)
        )
        active_now = [c for c in campaigns if _is_effectively_active(c, recent_activity_ids)]
        budget_now = round(sum(c.daily_budget or 0.0 for c in active_now), 2)
        active_count_now = len(active_now)

        if search:
            term = search.strip().lower()
            campaigns = [c for c in campaigns if term in (c.name or "").lower()]
        if status_filter == "active":
            campaigns = [c for c in campaigns if _is_effectively_active(c, recent_activity_ids)]
        elif status_filter == "paused":
            campaigns = [c for c in campaigns if not _is_active(c)]
```

(A Task 3 troca esse `elif status_filter == "paused":` de novo, pra cobrir os
4 valores — esta task só resolve `"active"`, que é o que os testes acima
cobrem.)

Trocar a chamada de `self._build_response(...)` dentro do loop que monta `responses` (procure `responses.append(` e o `self._build_response(` logo abaixo):

```python
            responses.append(
                self._build_response(
                    c, agg["spend"], agg["clicks"], agg["impressions"], comm, ad_rate, comm_rate, agg["reach"]
                )
            )
```

por:

```python
            responses.append(
                self._build_response(
                    c, agg["spend"], agg["clicks"], agg["impressions"], comm, ad_rate, comm_rate, agg["reach"],
                    recent_activity_ids=recent_activity_ids,
                )
            )
```

- [ ] **Step 6: Atualizar a rota `PATCH /{campaign_id}/status` pra passar `recent_activity_ids`**

Em `app/api/v1/routes/campaigns.py`, adicionar `timedelta` ao import de `datetime` (linha 3, hoje só `from datetime import date`):

```python
from datetime import date, timedelta
```

Adicionar o import de `RECENT_ACTIVITY_WINDOW_DAYS` (junto do import de `CampaignService`, linha ~23):

```python
from app.services.campaign_service import CampaignService, RECENT_ACTIVITY_WINDOW_DAYS
```

Trocar o fim de `update_status()` (linhas ~151-169):

```python
    new_status = await _fb_service(db).set_campaign_status(current_user.id, campaign.fb_campaign_id, payload.active)
    campaign.status = new_status
    campaign.effective_status = new_status
    db.commit()
    db.refresh(campaign)
    return _service(db)._build_response(campaign, 0.0, 0, 0, {})
```

por:

```python
    new_status = await _fb_service(db).set_campaign_status(current_user.id, campaign.fb_campaign_id, payload.active)
    campaign.status = new_status
    campaign.effective_status = new_status
    db.commit()
    db.refresh(campaign)
    recent_activity_ids = repo.campaign_ids_with_recent_activity(
        current_user.id, since=date.today() - timedelta(days=RECENT_ACTIVITY_WINDOW_DAYS)
    )
    return _service(db)._build_response(campaign, 0.0, 0, 0, {}, recent_activity_ids=recent_activity_ids)
```

- [ ] **Step 7: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_campaign_is_effectively_active.py -v`
Expected: PASS

- [ ] **Step 8: Rodar a suíte completa, com atenção especial a `test_campaign_active_count_orcamento_esgotado.py`**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de 3 falhas pré-existentes, nenhuma nova — os 4
testes de `test_campaign_active_count_orcamento_esgotado.py` continuam
passando sem alteração (a lógica de `_still_delivering`/`active_count_now`
não mudou, só foi movida pra dentro de `_is_effectively_active`).

- [ ] **Step 9: Commit**

```bash
git add app/services/campaign_service.py app/api/v1/routes/campaigns.py tests/unit/test_campaign_is_effectively_active.py
git commit -m "fix(campanhas): unifica critério de 'ativa' (card, filtro e toggle usam a mesma regra)"
```

## Task 3: Filtro de status com os 4 valores reais

**Depends on:** Task 2 (`_is_effectively_active`).

**Files:**
- Modify: `app/services/campaign_service.py:32` (`STATUS_FILTERS`), área de `_is_active`/`_is_effectively_active` (nova `_is_paused`), `:274-291` (filtro em `list_campaigns`)
- Modify: `app/api/v1/routes/campaigns.py:38-40` (descrição do `Query` de status)
- Test: `tests/unit/test_campaign_status_filter_4_valores.py`

**Interfaces:**
- Produces: `_is_paused(campaign: Campaign) -> bool` — `True` quando `effective_status`/`status` é uma variante de pausa (`PAUSED`, `CAMPAIGN_PAUSED`, `ADSET_PAUSED`).
- `STATUS_FILTERS = ("all", "active", "inactive", "paused", "archived")`.

- [ ] **Step 1: Escrever o teste que falha**

```python
"""Filtro de status da lista de Campanhas ganha os 4 valores reais pedidos
no doc (active/inactive/paused/archived) — hoje só aceita active/paused,
e "paused" hoje captura TUDO que não é active (inclusive zumbi e
arquivada), sem distinguir."""
from datetime import date, datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.campaign import Campaign, CampaignDailyInsight
from app.models.facebook_integration import FacebookIntegration
from app.models.user_settings import UserSettings
from app.repositories.campaign_repository import CampaignRepository
from app.services.campaign_service import CampaignService, STATUS_FILTERS, _is_paused

USUARIA = 1
CONTA = "act_111"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Campaign.__table__.create(engine)
    CampaignDailyInsight.__table__.create(engine)
    FacebookIntegration.__table__.create(engine)
    UserSettings.__table__.create(engine)
    sessao = sessionmaker(bind=engine)()
    yield sessao
    sessao.close()


def _campanha(db, **campos):
    padrao = dict(
        user_id=USUARIA, fb_campaign_id="fb", name="Campanha", ad_account_id=CONTA,
        status="ACTIVE", effective_status="ACTIVE",
    )
    padrao.update(campos)
    c = Campaign(**padrao)
    db.add(c)
    db.flush()
    return c


def _insight(db, campaign, dias_atras, spend=10.0):
    db.add(CampaignDailyInsight(
        user_id=USUARIA, campaign_id=campaign.id, fb_campaign_id=campaign.fb_campaign_id,
        date=date.today() - timedelta(days=dias_atras), spend=spend, clicks=1, impressions=10,
    ))


def test_status_filters_tem_os_4_valores_mais_all():
    assert set(STATUS_FILTERS) == {"all", "active", "inactive", "paused", "archived"}


def test_is_paused_reconhece_variantes_de_pausa():
    assert _is_paused(Campaign(effective_status="PAUSED", status="PAUSED")) is True
    assert _is_paused(Campaign(effective_status="CAMPAIGN_PAUSED", status="ACTIVE")) is True
    assert _is_paused(Campaign(effective_status="ADSET_PAUSED", status="ACTIVE")) is True
    assert _is_paused(Campaign(effective_status="ACTIVE", status="ACTIVE")) is False
    assert _is_paused(Campaign(effective_status="ARCHIVED", status="ARCHIVED")) is False


def test_filtro_archived_traz_so_arquivadas(db):
    velha = timedelta(days=1)
    arquivada = _campanha(db, fb_campaign_id="arq", effective_status="ARCHIVED", status="ARCHIVED")
    _insight(db, arquivada, dias_atras=1)
    ativa = _campanha(db, fb_campaign_id="ativa2")
    _insight(db, ativa, dias_atras=1)
    import json
    db.add(FacebookIntegration(
        user_id=USUARIA, encrypted_access_token="x", ad_accounts_json=json.dumps([CONTA]),
    ))
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA, status_filter="archived")

    assert [c.fb_campaign_id for c in resp.campaigns] == ["arq"]


def test_filtro_inactive_traz_zumbi_mas_nao_arquivada_nem_pausada(db):
    velha = timedelta(days=40)
    zumbi = _campanha(
        db, fb_campaign_id="zumbi2", lifetime_budget=12.0,
        created_at=datetime.now(timezone.utc) - velha,
        status_active_since=datetime.now(timezone.utc) - velha,
    )
    _insight(db, zumbi, dias_atras=38, spend=11.8)
    arquivada = _campanha(db, fb_campaign_id="arq2", effective_status="ARCHIVED", status="ARCHIVED")
    _insight(db, arquivada, dias_atras=1)
    pausada = _campanha(db, fb_campaign_id="pausada2", effective_status="PAUSED", status="PAUSED")
    _insight(db, pausada, dias_atras=1)
    import json
    db.add(FacebookIntegration(
        user_id=USUARIA, encrypted_access_token="x", ad_accounts_json=json.dumps([CONTA]),
    ))
    db.commit()

    resp = CampaignService(CampaignRepository(db)).list_campaigns(USUARIA, status_filter="inactive")

    assert [c.fb_campaign_id for c in resp.campaigns] == ["zumbi2"]
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_campaign_status_filter_4_valores.py -v`
Expected: FAIL — `ImportError: cannot import name '_is_paused'`.

- [ ] **Step 3: Aplicar o fix**

Trocar `STATUS_FILTERS` (linha 32):

```python
STATUS_FILTERS = ("all", "active", "paused")
```

por:

```python
STATUS_FILTERS = ("all", "active", "inactive", "paused", "archived")

_PAUSED_STATUSES = {"PAUSED", "CAMPAIGN_PAUSED", "ADSET_PAUSED"}
```

Adicionar `_is_paused`, logo depois de `_is_effectively_active` (criada na Task 2):

```python
def _is_paused(campaign: Campaign) -> bool:
    eff = (campaign.effective_status or campaign.status or "").upper()
    return eff in _PAUSED_STATUSES
```

Trocar o bloco de filtro em `list_campaigns()` (resultado da Task 2, Step 5):

```python
        if status_filter == "active":
            campaigns = [c for c in campaigns if _is_effectively_active(c, recent_activity_ids)]
        elif status_filter == "paused":
            campaigns = [c for c in campaigns if not _is_active(c)]
```

por:

```python
        if status_filter == "active":
            campaigns = [c for c in campaigns if _is_effectively_active(c, recent_activity_ids)]
        elif status_filter == "inactive":
            campaigns = [
                c for c in campaigns
                if _is_active(c) and not _is_effectively_active(c, recent_activity_ids)
            ]
        elif status_filter == "paused":
            campaigns = [c for c in campaigns if _is_paused(c)]
        elif status_filter == "archived":
            campaigns = [c for c in campaigns if (c.effective_status or "").upper() == "ARCHIVED"]
```

Em `app/api/v1/routes/campaigns.py`, trocar a descrição do `Query` (linha ~40):

```python
    status: str = Query(default="all", description="all | active | paused"),
```

por:

```python
    status: str = Query(default="all", description="all | active | inactive | paused | archived"),
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_campaign_status_filter_4_valores.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de 3 falhas pré-existentes, nenhuma nova.

- [ ] **Step 6: Commit**

```bash
git add app/services/campaign_service.py app/api/v1/routes/campaigns.py tests/unit/test_campaign_status_filter_4_valores.py
git commit -m "feat(campanhas): filtro de status com os 4 valores reais (active/inactive/paused/archived)"
```

## Task 4 (opcional — sinalizada no spec como adição além do doc): Backend rejeita PATCH em campanha arquivada

**Depends on:** Task 1 (campanha precisa chegar com `effective_status=ARCHIVED` de verdade pra esse caso ser alcançável).

O documento original só pede bloqueio de UI (Task 5 cobre isso sozinha). Esta task é uma camada extra de defesa que o design sinalizou como decisão do usuário — **pular esta task inteira se a decisão for ficar só no bloqueio de frontend**.

**Files:**
- Modify: `app/api/v1/routes/campaigns.py:151-169` (`update_status`)
- Test: `tests/unit/test_campaign_patch_status_bloqueia_arquivada.py`

- [ ] **Step 1: Escrever o teste que falha**

```python
"""PATCH /{campaign_id}/status rejeita (400) tentativa de mudar status de
campanha arquivada — bloqueio de UI sozinho não impede uma chamada direta
à API, e a Meta não permite reativar uma campanha arquivada do jeito que
este endpoint tenta hoje."""
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1.routes.campaigns import update_status
from app.models.campaign import Campaign
from app.schemas.campaign import CampaignStatusUpdate


@pytest.mark.asyncio
async def test_patch_status_rejeita_campanha_arquivada():
    campanha = Campaign(
        id=1, user_id=7, fb_campaign_id="fb1", name="Arquivada",
        ad_account_id="act_1", status="ARCHIVED", effective_status="ARCHIVED",
        created_at=datetime.now(timezone.utc),
    )
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = campanha
    usuario = MagicMock(id=7)

    with pytest.raises(HTTPException) as exc:
        await update_status(1, CampaignStatusUpdate(active=True), usuario, db)

    assert exc.value.status_code == 400
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_campaign_patch_status_bloqueia_arquivada.py -v`
Expected: FAIL — nenhuma `HTTPException` é levantada hoje (o endpoint tenta mudar o status normalmente).

- [ ] **Step 3: Aplicar o fix**

Em `update_status()` (`app/api/v1/routes/campaigns.py`), depois do `if not campaign:` e antes da chamada a `_fb_service`:

```python
    repo = CampaignRepository(db)
    campaign = repo.get_by_id(campaign_id, current_user.id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")
```

trocar por:

```python
    repo = CampaignRepository(db)
    campaign = repo.get_by_id(campaign_id, current_user.id)
    if not campaign:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Campanha não encontrada.")
    if (campaign.effective_status or "").upper() == "ARCHIVED":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Campanha arquivada não pode ser reativada por aqui.",
        )
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_campaign_patch_status_bloqueia_arquivada.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte completa**

Run: `PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q`
Expected: mesmo baseline de 3 falhas pré-existentes, nenhuma nova.

- [ ] **Step 6: Commit**

```bash
git add app/api/v1/routes/campaigns.py tests/unit/test_campaign_patch_status_bloqueia_arquivada.py
git commit -m "fix(campanhas): PATCH /status rejeita campanha arquivada (400) — defesa além do bloqueio de UI"
```

## Task 5: Frontend — toggle bloqueado + tooltip para arquivada

**Depends on:** Task 2 (campo `effective_status` já chega correto pro frontend — na verdade já chegava, esta task só passou a ser visível/coerente depois da Task 1+2).

**Files:**
- Modify: `src/shared/types/campaign.ts:77` (`CampaignStatusFilter`)
- Modify: `src/features/dashboard/pages/Campanhas.tsx:686-712` (toggle + tooltip)

Sem teste automatizado — verificação visual manual (documentar no relatório
se não for possível rodar `npm run dev` com sessão real neste ambiente).

- [ ] **Step 1: Expandir o tipo `CampaignStatusFilter`**

Em `src/shared/types/campaign.ts`, trocar (linha 77):

```tsx
export type CampaignStatusFilter = "all" | "active" | "paused";
```

por:

```tsx
export type CampaignStatusFilter = "all" | "active" | "inactive" | "paused" | "archived";
```

- [ ] **Step 2: Bloquear o toggle e mostrar tooltip pra campanha arquivada**

Em `src/features/dashboard/pages/Campanhas.tsx`, dentro do componente que renderiza o card de campanha (onde `campaign: Campaign` está em escopo, perto de `handleToggleActive`), adicionar antes do `return`:

```tsx
  const isArchived = campaign.effective_status === "ARCHIVED";
```

Trocar o bloco do toggle (linhas 693-711):

```tsx
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span>
                    <Switch
                      checked={campaign.is_active}
                      disabled={busy || !controlsEnabled}
                      onCheckedChange={handleToggleActive}
                      aria-label="Ativar/pausar campanha"
                    />
                  </span>
                </TooltipTrigger>
                {!controlsEnabled && (
                  <TooltipContent>
                    Conecte sua conta do Facebook para controlar campanhas.
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
```

por:

```tsx
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span>
                    <Switch
                      checked={campaign.is_active}
                      disabled={busy || !controlsEnabled || isArchived}
                      onCheckedChange={handleToggleActive}
                      aria-label="Ativar/pausar campanha"
                    />
                  </span>
                </TooltipTrigger>
                {isArchived ? (
                  <TooltipContent>
                    Campanha arquivada — não é possível reativar por aqui.
                  </TooltipContent>
                ) : (
                  !controlsEnabled && (
                    <TooltipContent>
                      Conecte sua conta do Facebook para controlar campanhas.
                    </TooltipContent>
                  )
                )}
              </Tooltip>
            </TooltipProvider>
```

- [ ] **Step 3: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos (erros pré-existentes em `DashboardClicks.tsx`/`adspends.service.ts`, se aparecerem, não são desta task).

- [ ] **Step 4: Verificação visual (manual)**

Run: `npm run dev`, abrir `/dashboard/campanhas`, achar (ou simular via
dado de teste) uma campanha arquivada — conferir que o toggle aparece
desabilitado e o tooltip mostra "Campanha arquivada — não é possível
reativar por aqui." Se não for possível testar com dado real neste
ambiente, documentar isso no relatório.

- [ ] **Step 5: Commit**

```bash
git add src/shared/types/campaign.ts src/features/dashboard/pages/Campanhas.tsx
git commit -m "fix(campanhas): toggle bloqueado + tooltip para campanha arquivada"
```

## Task 6: Frontend — dropdown do filtro de status com as 4 opções

**Depends on:** Task 3 (backend aceita os 4 valores em `status_filter`), Task 5 (mesmo arquivo `campaign.ts`, mesma mudança de tipo já feita).

**Files:**
- Modify: `src/features/dashboard/pages/Campanhas.tsx:327-336` (dropdown do filtro de status)

- [ ] **Step 1: Adicionar as 2 opções novas ao dropdown**

Trocar (linhas 327-336):

```tsx
              <Select value={status} onValueChange={(v) => setStatus(v as CampaignStatusFilter)}>
                <SelectTrigger className="flex-1 sm:flex-none sm:w-[120px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todas</SelectItem>
                  <SelectItem value="active">Ativa</SelectItem>
                  <SelectItem value="paused">Pausada</SelectItem>
                </SelectContent>
              </Select>
```

por:

```tsx
              <Select value={status} onValueChange={(v) => setStatus(v as CampaignStatusFilter)}>
                <SelectTrigger className="flex-1 sm:flex-none sm:w-[140px]">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">Todas</SelectItem>
                  <SelectItem value="active">Ativa</SelectItem>
                  <SelectItem value="inactive">Inativa</SelectItem>
                  <SelectItem value="paused">Pausada</SelectItem>
                  <SelectItem value="archived">Arquivada</SelectItem>
                </SelectContent>
              </Select>
```

(Largura do `SelectTrigger` ajustada de `120px` pra `140px` — "Arquivada" é
a label mais longa das 5; conferir visualmente que não trunca.)

- [ ] **Step 2: Type check e lint**

Run: `npx tsc --noEmit && npm run lint`
Expected: sem erros novos.

- [ ] **Step 3: Verificação visual (manual)**

Run: `npm run dev`, abrir `/dashboard/campanhas`, conferir que o dropdown
mostra as 5 opções (Todas/Ativa/Inativa/Pausada/Arquivada) sem truncar, e
que trocar o filtro realmente muda a lista (depende do backend das Tasks
2-3 já estarem no ar). Documentar no relatório se não for possível testar
neste ambiente.

- [ ] **Step 4: Commit**

```bash
git add src/features/dashboard/pages/Campanhas.tsx
git commit -m "feat(campanhas): dropdown do filtro de status com as 4 opções reais"
```

---

## Self-Review (spec coverage)

- **Parte 1 do spec** (sync enxerga arquivada) → Task 1. ✅
- **Parte 2 do spec** (função única, 3 lugares) → Task 2. ✅
- **Parte 3 do spec** (toggle bloqueado, backend opcional) → Task 5 (obrigatória) + Task 4 (opcional, claramente sinalizada). ✅
- **Parte 4 do spec** (filtro 4 valores) → Task 3 (backend) + Task 6 (frontend). ✅
- Global Constraints do spec (não mexer em markup/imposto/ROAS, não quebrar heurística de 7 dias, DELETED fora de escopo) → nenhuma task toca `_compute_metrics`/ROAS; `_still_delivering` só foi movida pra dentro de `_is_effectively_active`, lógica interna intacta (Task 2, Step 8 pede rodar os 4 testes de regressão existentes); `DELETED` explicitamente fora do filtro da Task 1.
