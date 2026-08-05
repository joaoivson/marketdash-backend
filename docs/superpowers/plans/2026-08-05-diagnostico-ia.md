# Diagnóstico IA — plano de implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menu novo onde a aluna gera, com um clique, uma análise da operação no período — relatório estruturado, chat sobre ele e histórico —, com a matemática vindo do backend e a IA apenas narrando.

**Architecture:** Geração síncrona na requisição (sem Celery), presa a um snapshot congelado dos dados. O backend classifica campanhas com o `_health()` que já existe; a IA recebe números prontos e escreve o texto. Créditos derivados de um ledger, debitados só no sucesso.

**Tech Stack:** FastAPI, SQLAlchemy, PostgreSQL (Supabase), OpenAI `gpt-4o-mini` via HTTP (`httpx`, já no requirements), React + Vite + Zustand + shadcn/ui.

## Global Constraints

- **Deploy só em `develop`.** Nunca `main`. Migration aplicada **só** no banco de homologação (`ytjpdvjuxtvxacredekk`). O `.env` local já aponta para lá.
- **A IA nunca calcula.** Todo número que chega no prompt já vem calculado e classificado pelo backend. O prompt de sistema proíbe recalcular.
- **Estado terminal sempre.** Uma sessão nunca fica em `gerando`. Toda falha grava `erro` com mensagem.
- **Débito só no sucesso.** Falha da IA não consome crédito.
- **Nunca criar query param chamado `user_id`** — `fetchWithAuth` no frontend já anexa `user_id=user_N` em toda request e o backend rejeitaria com 422. Use outro nome (padrão do projeto: `filter_user_id`).
- **Camadas:** `routes → services → repositories → models`. Não pular camadas.
- Cotas: gerar = 10 créditos, chat = 1. Essencial 0, Pro 200, Max 1000. Reset dia 1º, sem acúmulo.
- Teto de 20 mensagens por sessão de chat.
- Testes em `tests/unit/`, sem banco (fakes/monkeypatch). Baseline atual: 224 passando, 3 falhas pré-existentes em `test_shopee_upsert_additive.py` (não são regressão).
- Nomes de código e comentários em português, seguindo o padrão recente do projeto.

---

## Estrutura de arquivos

**Backend (`marketdash-backend/`)**

| Arquivo | Responsabilidade |
|---|---|
| `migrations/043_diagnostico_ia.sql` | 3 tabelas novas |
| `app/models/ai_diagnostic.py` | `AiDiagnostic`, `AiDiagnosticMessage` |
| `app/models/ai_credit_ledger.py` | `AiCreditLedger` |
| `app/repositories/ai_diagnostic_repository.py` | acesso a sessões e mensagens |
| `app/repositories/ai_credit_repository.py` | acesso ao ledger |
| `app/services/ai_credit_service.py` | saldo, cota, débito |
| `app/services/ai_snapshot_service.py` | monta o retrato dos dados (sem IA) |
| `app/services/openai_client.py` | única fronteira com a OpenAI |
| `app/services/ai_diagnostic_service.py` | orquestra tudo |
| `app/schemas/ai_diagnostic.py` | contratos de request/response |
| `app/api/v1/routes/ai_diagnostics.py` | endpoints |
| `app/core/plans.py` | menu `diagnostico_ia` + limite `creditos_ia` |

**Frontend (`marketdash-frontend/`)**

| Arquivo | Responsabilidade |
|---|---|
| `src/services/ai-diagnostic.service.ts` | chamadas e tipos |
| `src/features/diagnostico/pages/DiagnosticoIA.tsx` | tela: gerar, histórico, sessão |
| `src/features/diagnostico/components/RelatorioDiagnostico.tsx` | render do relatório + print |
| `src/features/diagnostico/components/ChatDiagnostico.tsx` | conversa |
| `src/features/diagnostico/components/SaldoCreditos.tsx` | saldo e CTA |
| `src/features/diagnostico/print.css` | CSS de impressão do PDF |

---

## Task 1: Migration e models

**Files:**
- Create: `migrations/043_diagnostico_ia.sql`
- Create: `app/models/ai_diagnostic.py`
- Create: `app/models/ai_credit_ledger.py`
- Modify: `app/models/__init__.py`
- Test: `tests/unit/test_ai_models.py`

**Interfaces:**
- Produces: `AiDiagnostic` (campos `id, user_id, periodo_inicio, periodo_fim, snapshot, relatorio, status, erro_mensagem, modelo, tokens_entrada, tokens_saida, criado_em, concluido_em`), `AiDiagnosticMessage` (`id, diagnostic_id, papel, conteudo, criado_em`), `AiCreditLedger` (`id, user_id, diagnostic_id, tipo, creditos, saldo_apos, criado_em`).
- Constantes de status: `STATUS_GERANDO = "gerando"`, `STATUS_PRONTO = "pronto"`, `STATUS_ERRO = "erro"`.

- [ ] **Step 1: Escrever a migration**

```sql
-- Migration 043: Diagnóstico IA
--
-- Três tabelas. O saldo de créditos NÃO é guardado em contador: é derivado da
-- soma do ledger no mês corrente. Contador diverge; ledger audita.

CREATE TABLE IF NOT EXISTS ai_diagnostics (
  id              BIGSERIAL PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  periodo_inicio  DATE NOT NULL,
  periodo_fim     DATE NOT NULL,
  snapshot        JSONB NOT NULL DEFAULT '{}'::jsonb,
  relatorio       JSONB,
  status          TEXT NOT NULL DEFAULT 'gerando',
  erro_mensagem   TEXT,
  modelo          TEXT,
  tokens_entrada  INTEGER,
  tokens_saida    INTEGER,
  criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  concluido_em    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_ai_diagnostics_user ON ai_diagnostics (user_id, criado_em DESC);

CREATE TABLE IF NOT EXISTS ai_diagnostic_messages (
  id             BIGSERIAL PRIMARY KEY,
  diagnostic_id  BIGINT NOT NULL REFERENCES ai_diagnostics(id) ON DELETE CASCADE,
  papel          TEXT NOT NULL,
  conteudo       TEXT NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ai_messages_diag ON ai_diagnostic_messages (diagnostic_id, criado_em);

CREATE TABLE IF NOT EXISTS ai_credit_ledger (
  id             BIGSERIAL PRIMARY KEY,
  user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  diagnostic_id  BIGINT REFERENCES ai_diagnostics(id) ON DELETE SET NULL,
  tipo           TEXT NOT NULL,
  creditos       INTEGER NOT NULL,
  saldo_apos     INTEGER NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ai_ledger_user_mes ON ai_credit_ledger (user_id, criado_em DESC);
```

- [ ] **Step 2: Escrever o teste dos models**

```python
# tests/unit/test_ai_models.py
"""Os models do Diagnóstico IA batem com a migration 043."""
from app.models.ai_diagnostic import (
    STATUS_ERRO, STATUS_GERANDO, STATUS_PRONTO, AiDiagnostic, AiDiagnosticMessage,
)
from app.models.ai_credit_ledger import AiCreditLedger


def test_tabelas_com_os_nomes_da_migration():
    assert AiDiagnostic.__tablename__ == "ai_diagnostics"
    assert AiDiagnosticMessage.__tablename__ == "ai_diagnostic_messages"
    assert AiCreditLedger.__tablename__ == "ai_credit_ledger"


def test_colunas_do_diagnostico():
    cols = set(AiDiagnostic.__table__.columns.keys())
    assert {"user_id", "periodo_inicio", "periodo_fim", "snapshot", "relatorio",
            "status", "erro_mensagem", "modelo", "tokens_entrada", "tokens_saida",
            "criado_em", "concluido_em"} <= cols


def test_colunas_do_ledger():
    cols = set(AiCreditLedger.__table__.columns.keys())
    assert {"user_id", "diagnostic_id", "tipo", "creditos", "saldo_apos"} <= cols


def test_status_comeca_em_gerando():
    assert AiDiagnostic.__table__.c.status.default.arg == STATUS_GERANDO
    assert (STATUS_GERANDO, STATUS_PRONTO, STATUS_ERRO) == ("gerando", "pronto", "erro")
```

- [ ] **Step 3: Rodar o teste e ver falhar**

Run: `cd marketdash-backend && source .venv312/bin/activate && python -m pytest tests/unit/test_ai_models.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.ai_diagnostic'`

- [ ] **Step 4: Escrever os models**

```python
# app/models/ai_diagnostic.py
"""Sessão de diagnóstico e as mensagens do chat dela."""
from sqlalchemy import (
    BigInteger, Column, Date, DateTime, ForeignKey, Integer, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.db.base import Base

STATUS_GERANDO = "gerando"
STATUS_PRONTO = "pronto"
STATUS_ERRO = "erro"

PAPEL_USUARIA = "user"
PAPEL_IA = "assistant"


class AiDiagnostic(Base):
    __tablename__ = "ai_diagnostics"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    periodo_inicio = Column(Date, nullable=False)
    periodo_fim = Column(Date, nullable=False)
    # snapshot: os números CONGELADOS. O chat lê daqui, nunca de dados frescos —
    # é o que garante que a conversa nunca contradiga o PDF.
    snapshot = Column(JSONB, nullable=False, server_default="{}")
    relatorio = Column(JSONB, nullable=True)
    status = Column(Text, nullable=False, default=STATUS_GERANDO)
    erro_mensagem = Column(Text, nullable=True)
    modelo = Column(Text, nullable=True)
    tokens_entrada = Column(Integer, nullable=True)
    tokens_saida = Column(Integer, nullable=True)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    concluido_em = Column(DateTime(timezone=True), nullable=True)


class AiDiagnosticMessage(Base):
    __tablename__ = "ai_diagnostic_messages"

    id = Column(BigInteger, primary_key=True, index=True)
    diagnostic_id = Column(
        BigInteger, ForeignKey("ai_diagnostics.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    papel = Column(Text, nullable=False)
    conteudo = Column(Text, nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

```python
# app/models/ai_credit_ledger.py
"""
Extrato de créditos de IA.

Saldo é DERIVADO da soma do mês corrente, não guardado num contador: contador
diverge silenciosamente, extrato permite auditar por que uma aluna zerou.
"""
from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, Text
from sqlalchemy.sql import func

from app.db.base import Base

TIPO_GERACAO = "geracao"
TIPO_CHAT = "chat"


class AiCreditLedger(Base):
    __tablename__ = "ai_credit_ledger"

    id = Column(BigInteger, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    diagnostic_id = Column(
        BigInteger, ForeignKey("ai_diagnostics.id", ondelete="SET NULL"), nullable=True,
    )
    tipo = Column(Text, nullable=False)
    creditos = Column(Integer, nullable=False)
    saldo_apos = Column(Integer, nullable=False)
    criado_em = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
```

- [ ] **Step 5: Registrar no `app/models/__init__.py`**

Adicionar ao final do arquivo, seguindo o padrão dos imports já existentes:

```python
from app.models.ai_diagnostic import AiDiagnostic, AiDiagnosticMessage  # noqa: F401
from app.models.ai_credit_ledger import AiCreditLedger  # noqa: F401
```

- [ ] **Step 6: Rodar o teste e ver passar**

Run: `python -m pytest tests/unit/test_ai_models.py -q`
Expected: PASS — 4 passed

- [ ] **Step 7: Commit**

```bash
git add migrations/043_diagnostico_ia.sql app/models/ai_diagnostic.py \
        app/models/ai_credit_ledger.py app/models/__init__.py tests/unit/test_ai_models.py
git commit -m "feat(ia): migration e models do Diagnóstico IA

Saldo de crédito derivado do ledger, não de contador: contador diverge
em silêncio, extrato permite auditar por que uma aluna zerou."
```

---

## Task 2: Serviço de créditos

**Files:**
- Create: `app/repositories/ai_credit_repository.py`
- Create: `app/services/ai_credit_service.py`
- Modify: `app/core/plans.py`
- Test: `tests/unit/test_ai_credit_service.py`

**Interfaces:**
- Consumes: `AiCreditLedger`, `TIPO_GERACAO`, `TIPO_CHAT` (Task 1).
- Produces:
  - `CUSTO_GERACAO = 10`, `CUSTO_CHAT = 1`
  - `AiCreditService(db).cota(plano: str) -> int`
  - `AiCreditService(db).saldo(user_id: int, plano: str) -> int`
  - `AiCreditService(db).tem_saldo(user_id: int, plano: str, custo: int) -> bool`
  - `AiCreditService(db).debitar(user_id, plano, tipo, creditos, diagnostic_id=None) -> int` (devolve saldo restante; levanta `SaldoInsuficiente`)
  - Exceção `SaldoInsuficiente(Exception)` com atributos `saldo` e `necessario`

- [ ] **Step 1: Escrever o teste**

```python
# tests/unit/test_ai_credit_service.py
"""
Créditos do Diagnóstico IA.

Saldo é derivado do ledger do mês corrente. Reset é implícito: virou o mês,
a soma recomeça — não existe job de reset pra falhar.
"""
from datetime import datetime, timezone

import pytest

from app.services.ai_credit_service import (
    CUSTO_CHAT, CUSTO_GERACAO, AiCreditService, SaldoInsuficiente,
)


class _FakeLedgerRepo:
    def __init__(self, gasto_no_mes=0):
        self._gasto = gasto_no_mes
        self.gravados = []

    def total_gasto_no_mes(self, user_id, inicio_do_mes):
        return self._gasto

    def registrar(self, user_id, diagnostic_id, tipo, creditos, saldo_apos):
        self.gravados.append(
            {"user_id": user_id, "diagnostic_id": diagnostic_id, "tipo": tipo,
             "creditos": creditos, "saldo_apos": saldo_apos}
        )


def _servico(gasto=0):
    return AiCreditService(repo=_FakeLedgerRepo(gasto))


def test_cota_por_plano():
    s = _servico()
    assert s.cota("essencial") == 0
    assert s.cota("pro") == 200
    assert s.cota("max") == 1000


def test_plano_desconhecido_cai_no_minimo():
    assert _servico().cota("plano_inventado") == 0


def test_saldo_e_cota_menos_gasto_do_mes():
    assert _servico(gasto=30).saldo(1, "pro") == 170


def test_saldo_nunca_fica_negativo():
    assert _servico(gasto=500).saldo(1, "pro") == 0


def test_essencial_nao_tem_saldo():
    s = _servico()
    assert s.saldo(1, "essencial") == 0
    assert s.tem_saldo(1, "essencial", CUSTO_GERACAO) is False


def test_tem_saldo_na_fronteira_exata():
    s = _servico(gasto=190)   # sobram 10, custo da geração é 10
    assert s.tem_saldo(1, "pro", CUSTO_GERACAO) is True
    s2 = _servico(gasto=191)
    assert s2.tem_saldo(1, "pro", CUSTO_GERACAO) is False


def test_debitar_grava_no_extrato_e_devolve_saldo():
    repo = _FakeLedgerRepo(gasto_no_mes=0)
    s = AiCreditService(repo=repo)
    restante = s.debitar(1, "pro", "geracao", CUSTO_GERACAO, diagnostic_id=7)
    assert restante == 190
    assert repo.gravados == [
        {"user_id": 1, "diagnostic_id": 7, "tipo": "geracao",
         "creditos": 10, "saldo_apos": 190}
    ]


def test_debitar_sem_saldo_levanta_e_nao_grava():
    repo = _FakeLedgerRepo(gasto_no_mes=195)
    s = AiCreditService(repo=repo)
    with pytest.raises(SaldoInsuficiente) as exc:
        s.debitar(1, "pro", "geracao", CUSTO_GERACAO)
    assert exc.value.saldo == 5
    assert exc.value.necessario == 10
    assert repo.gravados == []


def test_custos_conforme_spec():
    assert (CUSTO_GERACAO, CUSTO_CHAT) == (10, 1)
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/test_ai_credit_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai_credit_service'`

- [ ] **Step 3: Adicionar a cota em `app/core/plans.py`**

Em `FEATURES`, acrescentar a chave `creditos_ia` em `limites` e o menu novo. Nos três planos:

```python
    "essencial": {
        "menus": frozenset(
            {"dashboard", "campanhas", "upload_cliques", "indique_ganhe", "configuracoes", "planos"}
        ),
        "limites": {"paginas_captura": 0, "links": 0, "creditos_ia": 0},
        "label": "Essencial",
    },
    "pro": {
        "menus": frozenset(
            {
                "dashboard", "campanhas", "upload_cliques", "captura", "meus_links",
                "diagnostico_ia", "indique_ganhe", "configuracoes", "planos",
            }
        ),
        "limites": {"paginas_captura": 15, "links": 30, "creditos_ia": 200},
        "label": "Pro",
    },
    "max": {
        "menus": frozenset(
            {
                "dashboard", "campanhas", "upload_cliques", "captura", "meus_links",
                "diagnostico_ia", "indique_ganhe", "configuracoes", "planos",
            }
        ),
        "limites": {"paginas_captura": 50, "links": 100, "creditos_ia": 1000},
        "label": "Max",
    },
```

E incluir o menu no cadeado do Essencial:

```python
PRO_ONLY_MENUS: FrozenSet[str] = frozenset({"captura", "meus_links", "diagnostico_ia"})
```

- [ ] **Step 4: Escrever o repository**

```python
# app/repositories/ai_credit_repository.py
"""Acesso ao extrato de créditos de IA."""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_credit_ledger import AiCreditLedger


class AiCreditRepository:
    def __init__(self, db: Session):
        self.db = db

    def total_gasto_no_mes(self, user_id: int, inicio_do_mes: datetime) -> int:
        total = (
            self.db.query(func.coalesce(func.sum(AiCreditLedger.creditos), 0))
            .filter(
                AiCreditLedger.user_id == user_id,
                AiCreditLedger.criado_em >= inicio_do_mes,
            )
            .scalar()
        )
        return int(total or 0)

    def registrar(self, user_id, diagnostic_id, tipo, creditos, saldo_apos) -> AiCreditLedger:
        linha = AiCreditLedger(
            user_id=user_id,
            diagnostic_id=diagnostic_id,
            tipo=tipo,
            creditos=creditos,
            saldo_apos=saldo_apos,
        )
        self.db.add(linha)
        self.db.commit()
        return linha
```

- [ ] **Step 5: Escrever o serviço**

```python
# app/services/ai_credit_service.py
"""
Saldo e débito de créditos de IA.

O saldo é DERIVADO: cota do plano menos o que já foi gasto no mês corrente.
Não existe contador guardado nem job de reset — virou o mês, a soma recomeça
sozinha. Um job de reset é mais uma coisa pra falhar em silêncio.
"""
from datetime import datetime, timezone
from typing import Optional

from app.core.plans import plan_limit
from app.repositories.ai_credit_repository import AiCreditRepository

CUSTO_GERACAO = 10
CUSTO_CHAT = 1


class SaldoInsuficiente(Exception):
    def __init__(self, saldo: int, necessario: int):
        self.saldo = saldo
        self.necessario = necessario
        super().__init__(f"Saldo insuficiente: {saldo} disponível, {necessario} necessário")


def _inicio_do_mes() -> datetime:
    agora = datetime.now(timezone.utc)
    return agora.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


class AiCreditService:
    def __init__(self, repo: AiCreditRepository):
        self.repo = repo

    def cota(self, plano: str) -> int:
        return plan_limit(plano, "creditos_ia")

    def saldo(self, user_id: int, plano: str) -> int:
        gasto = self.repo.total_gasto_no_mes(user_id, _inicio_do_mes())
        return max(self.cota(plano) - gasto, 0)

    def tem_saldo(self, user_id: int, plano: str, custo: int) -> bool:
        return self.saldo(user_id, plano) >= custo

    def debitar(
        self,
        user_id: int,
        plano: str,
        tipo: str,
        creditos: int,
        diagnostic_id: Optional[int] = None,
    ) -> int:
        atual = self.saldo(user_id, plano)
        if atual < creditos:
            raise SaldoInsuficiente(saldo=atual, necessario=creditos)
        restante = atual - creditos
        self.repo.registrar(user_id, diagnostic_id, tipo, creditos, restante)
        return restante
```

- [ ] **Step 6: Rodar e ver passar**

Run: `python -m pytest tests/unit/test_ai_credit_service.py -q`
Expected: PASS — 9 passed

- [ ] **Step 7: Rodar a suíte inteira**

Run: `python -m pytest tests/unit -q`
Expected: 3 falhas pré-existentes em `test_shopee_upsert_additive.py`, resto passando

- [ ] **Step 8: Commit**

```bash
git add app/repositories/ai_credit_repository.py app/services/ai_credit_service.py \
        app/core/plans.py tests/unit/test_ai_credit_service.py
git commit -m "feat(ia): créditos derivados do ledger + menu diagnostico_ia nos planos

Sem contador e sem job de reset: saldo = cota do plano menos o gasto do mês
corrente. Virou o mês, a soma recomeça sozinha."
```

---

## Task 3: Snapshot dos dados

**Files:**
- Create: `app/services/ai_snapshot_service.py`
- Test: `tests/unit/test_ai_snapshot_service.py`

**Interfaces:**
- Consumes: `CampaignService.list_campaigns(user_id, start_date, end_date, status_filter, search) -> CampaignListResponse` e `DashboardService.get_kpis(db, user_id, filters) -> KPIs` (ambos já existem).
- Produces: `AiSnapshotService(db).montar(user_id: int, inicio: date, fim: date) -> dict` com as chaves `periodo`, `kpis`, `tops`, `campanhas`, `tem_meta`, `vazio`.

- [ ] **Step 1: Escrever o teste**

```python
# tests/unit/test_ai_snapshot_service.py
"""
Snapshot: o retrato dos números que vai pro prompt.

Precisa refletir EXATAMENTE a classificação do campaign_service — se o snapshot
divergir do dashboard, a IA vai narrar um número que a aluna não vê na tela.
"""
from datetime import date
from types import SimpleNamespace

from app.services.ai_snapshot_service import AiSnapshotService


def _campanha(nome, health, roas, spend, commission_net, profit, orders=0):
    return SimpleNamespace(
        name=nome, health=health, linked=True, is_active=True,
        fb_campaign_id="1", sub_id="sub",
        metrics=SimpleNamespace(
            roas=roas, spend=spend, spend_with_tax=spend, clicks=10, impressions=100,
            commission=commission_net, commission_net=commission_net, revenue=0.0,
            orders=orders, direct_orders=0, profit=profit, cpc=None, ctr=None, reach=0,
        ),
    )


class _FakeCampaignService:
    def __init__(self, campanhas):
        self._campanhas = campanhas

    def list_campaigns(self, user_id, start_date=None, end_date=None,
                       status_filter="all", search=None):
        return SimpleNamespace(campaigns=self._campanhas, has_tax=False,
                               kpis=SimpleNamespace(total_spend=0.0))


def _servico(campanhas=None, kpis=None, tops=None, tem_meta=True):
    svc = AiSnapshotService(db=None)
    svc._campanhas_do_periodo = lambda u, i, f: (campanhas or [])
    svc._kpis_do_periodo = lambda u, i, f: (kpis or {
        "comissao_liquida": 1000.0, "receita": 5000.0, "gasto": 400.0,
        "lucro": 600.0, "pedidos": 20,
    })
    svc._tops = lambda u, i, f: (tops or {"canal": [], "categoria": [], "sub_id": []})
    svc._tem_meta = lambda u: tem_meta
    return svc


def test_snapshot_tem_as_secoes_esperadas():
    s = _servico().montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert set(s) >= {"periodo", "kpis", "tops", "campanhas", "tem_meta", "vazio"}


def test_periodo_vem_no_snapshot():
    s = _servico().montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["periodo"] == {"inicio": "2026-08-01", "fim": "2026-08-05"}


def test_sem_meta_nao_cria_bloco_de_campanha():
    s = _servico(tem_meta=False).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["tem_meta"] is False
    assert s["campanhas"] == []


def test_campanhas_preservam_a_classificacao_do_backend():
    campanhas = [
        _campanha("escala", "healthy", 2.4, 100.0, 240.0, 140.0),
        _campanha("perde", "loss", 0.4, 100.0, 40.0, -60.0),
        _campanha("limite", "warning", 1.1, 100.0, 110.0, 10.0),
    ]
    s = _servico(campanhas).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    por_nome = {c["nome"]: c for c in s["campanhas"]}
    assert por_nome["escala"]["classificacao"] == "healthy"
    assert por_nome["perde"]["classificacao"] == "loss"
    assert por_nome["limite"]["classificacao"] == "warning"
    assert por_nome["perde"]["roas"] == 0.4
    assert por_nome["perde"]["lucro"] == -60.0


def test_periodo_sem_dado_marca_vazio():
    s = _servico(kpis={"comissao_liquida": 0.0, "receita": 0.0, "gasto": 0.0,
                       "lucro": 0.0, "pedidos": 0}).montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["vazio"] is True


def test_periodo_com_dado_nao_marca_vazio():
    s = _servico().montar(1, date(2026, 8, 1), date(2026, 8, 5))
    assert s["vazio"] is False


def test_snapshot_e_serializavel_em_json():
    import json
    s = _servico([_campanha("x", "healthy", 2.0, 10.0, 20.0, 10.0)]).montar(
        1, date(2026, 8, 1), date(2026, 8, 5))
    assert json.loads(json.dumps(s))["periodo"]["inicio"] == "2026-08-01"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/test_ai_snapshot_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai_snapshot_service'`

- [ ] **Step 3: Escrever o serviço**

```python
# app/services/ai_snapshot_service.py
"""
Monta o retrato dos números que a IA vai narrar.

Regra de ouro: aqui é onde a MATEMÁTICA acontece. Tudo que sai daqui já está
calculado e classificado — a IA recebe fatos e só escreve o texto. A
classificação de campanha vem inteira do campaign_service, então o que a IA
narra é exatamente o que a aluna vê na tela de Campanhas.
"""
from datetime import date
from typing import Any, Dict, List

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow
from app.repositories.campaign_repository import CampaignRepository
from app.repositories.facebook_integration_repository import FacebookIntegrationRepository
from app.schemas.dashboard import DashboardFilters
from app.services.campaign_service import CampaignService
from app.services.dashboard_service import DashboardService

LIMITE_TOP = 5


class AiSnapshotService:
    def __init__(self, db: Session):
        self.db = db

    # -- coleta -----------------------------------------------------------

    def _tem_meta(self, user_id: int) -> bool:
        integ = FacebookIntegrationRepository(self.db).get_by_user_id(user_id)
        return bool(integ and integ.is_active)

    def _campanhas_do_periodo(self, user_id: int, inicio: date, fim: date) -> List[Any]:
        svc = CampaignService(CampaignRepository(self.db))
        return svc.list_campaigns(user_id, start_date=inicio, end_date=fim).campaigns

    def _kpis_do_periodo(self, user_id: int, inicio: date, fim: date) -> Dict[str, float]:
        kpis = DashboardService.get_kpis(
            self.db, user_id, DashboardFilters(start_date=inicio, end_date=fim)
        )
        return {
            "comissao_liquida": round(kpis.total_commission, 2),
            "receita": round(kpis.total_revenue, 2),
            "gasto": round(kpis.total_cost, 2),
            "lucro": round(kpis.total_profit, 2),
            "pedidos": int(kpis.total_rows),
        }

    def _tops(self, user_id: int, inicio: date, fim: date) -> Dict[str, List[Dict[str, Any]]]:
        def agrupar(coluna):
            linhas = (
                self.db.query(
                    coluna.label("chave"),
                    func.coalesce(func.sum(DatasetRow.commission), 0).label("comissao"),
                    func.count(DatasetRow.id).label("pedidos"),
                )
                .filter(
                    DatasetRow.user_id == user_id,
                    DatasetRow.date >= inicio,
                    DatasetRow.date <= fim,
                    coluna.isnot(None),
                )
                .group_by(coluna)
                .order_by(func.coalesce(func.sum(DatasetRow.commission), 0).desc())
                .limit(LIMITE_TOP)
                .all()
            )
            return [
                {"nome": r.chave, "comissao": float(r.comissao or 0), "pedidos": int(r.pedidos)}
                for r in linhas
            ]

        return {
            "canal": agrupar(DatasetRow.channel),
            "categoria": agrupar(DatasetRow.category),
            "sub_id": agrupar(DatasetRow.sub_id1),
        }

    # -- montagem ---------------------------------------------------------

    def montar(self, user_id: int, inicio: date, fim: date) -> Dict[str, Any]:
        kpis = self._kpis_do_periodo(user_id, inicio, fim)
        tops = self._tops(user_id, inicio, fim)
        tem_meta = self._tem_meta(user_id)

        campanhas: List[Dict[str, Any]] = []
        if tem_meta:
            for c in self._campanhas_do_periodo(user_id, inicio, fim):
                m = c.metrics
                campanhas.append({
                    "nome": c.name,
                    # classificação do backend, intocada — a IA não reclassifica
                    "classificacao": c.health,
                    "ativa": bool(c.is_active),
                    "vinculada": bool(c.linked),
                    "roas": round(float(m.roas), 2),
                    "gasto": round(float(m.spend_with_tax), 2),
                    "comissao_liquida": round(float(m.commission_net), 2),
                    "lucro": round(float(m.profit), 2),
                    "pedidos": int(m.orders),
                    "cliques": int(m.clicks),
                })

        vazio = kpis["pedidos"] == 0 and kpis["comissao_liquida"] == 0 and not campanhas

        return {
            "periodo": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
            "kpis": kpis,
            "tops": tops,
            "campanhas": campanhas,
            "tem_meta": tem_meta,
            "vazio": vazio,
        }
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/test_ai_snapshot_service.py -q`
Expected: PASS — 7 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/ai_snapshot_service.py tests/unit/test_ai_snapshot_service.py
git commit -m "feat(ia): snapshot dos números para o diagnóstico

A classificação de campanha vem inteira do campaign_service — o que a IA
narra é exatamente o que a aluna vê na tela de Campanhas."
```

---

## Task 4: Cliente OpenAI isolado

**Files:**
- Create: `app/services/openai_client.py`
- Test: `tests/unit/test_openai_client.py`

**Interfaces:**
- Produces:
  - `ErroIA(Exception)` com atributo `motivo` (`"sem_chave" | "timeout" | "http" | "formato"`)
  - `OpenAiClient(api_key: str | None, modelo: str)`
  - `.disponivel() -> bool`
  - `.completar_json(sistema: str, usuario: str, timeout: float = 60.0) -> tuple[dict, int, int]` → `(json, tokens_entrada, tokens_saida)`
  - `.completar_texto(sistema: str, mensagens: list[dict], timeout: float = 60.0) -> tuple[str, int, int]`

- [ ] **Step 1: Escrever o teste**

```python
# tests/unit/test_openai_client.py
"""
Fronteira com a OpenAI.

Isolada de propósito: todo o resto do Diagnóstico IA é testado sem rede porque
só este arquivo fala com a API.
"""
import json

import httpx
import pytest

from app.services.openai_client import ErroIA, OpenAiClient


def _resposta(conteudo, entrada=100, saida=50):
    return httpx.Response(
        200,
        json={
            "choices": [{"message": {"content": conteudo}}],
            "usage": {"prompt_tokens": entrada, "completion_tokens": saida},
        },
    )


def _cliente(handler, api_key="sk-teste"):
    c = OpenAiClient(api_key=api_key, modelo="gpt-4o-mini")
    c._transport = httpx.MockTransport(handler)
    return c


def test_sem_chave_nao_esta_disponivel():
    assert OpenAiClient(api_key=None, modelo="gpt-4o-mini").disponivel() is False
    assert OpenAiClient(api_key="sk-x", modelo="gpt-4o-mini").disponivel() is True


def test_sem_chave_levanta_erro_tipado():
    c = OpenAiClient(api_key=None, modelo="gpt-4o-mini")
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "sem_chave"


def test_completar_json_devolve_dict_e_tokens():
    payload = {"resumo": "tudo certo", "escalar": []}
    c = _cliente(lambda req: _resposta(json.dumps(payload), 120, 80))
    dados, entrada, saida = c.completar_json("sistema", "usuario")
    assert dados == payload
    assert (entrada, saida) == (120, 80)


def test_json_invalido_levanta_erro_de_formato():
    c = _cliente(lambda req: _resposta("isso não é json"))
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "formato"


def test_erro_http_levanta_erro_tipado():
    c = _cliente(lambda req: httpx.Response(500, json={"error": "boom"}))
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "http"


def test_timeout_levanta_erro_tipado():
    def estoura(req):
        raise httpx.TimeoutException("demorou")
    c = _cliente(estoura)
    with pytest.raises(ErroIA) as exc:
        c.completar_json("sistema", "usuario")
    assert exc.value.motivo == "timeout"


def test_completar_texto_devolve_string():
    c = _cliente(lambda req: _resposta("resposta do chat", 10, 5))
    texto, entrada, saida = c.completar_texto(
        "sistema", [{"role": "user", "content": "oi"}]
    )
    assert texto == "resposta do chat"
    assert (entrada, saida) == (10, 5)


def test_modelo_vai_no_corpo_da_requisicao():
    capturado = {}

    def handler(req):
        capturado.update(json.loads(req.content))
        return _resposta(json.dumps({"ok": True}))

    _cliente(handler).completar_json("sistema", "usuario")
    assert capturado["model"] == "gpt-4o-mini"
    assert capturado["messages"][0]["role"] == "system"
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/test_openai_client.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.openai_client'`

- [ ] **Step 3: Escrever o cliente**

```python
# app/services/openai_client.py
"""
Única fronteira com a OpenAI.

Isolar aqui é o que permite testar todo o resto do Diagnóstico IA sem rede.
Todo erro sai tipado (ErroIA.motivo) para a camada de cima decidir o que
mostrar — e, principalmente, para NÃO debitar crédito quando a IA falha.
"""
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

URL = "https://api.openai.com/v1/chat/completions"


class ErroIA(Exception):
    def __init__(self, motivo: str, detalhe: str = ""):
        self.motivo = motivo
        self.detalhe = detalhe
        super().__init__(f"{motivo}: {detalhe}" if detalhe else motivo)


class OpenAiClient:
    def __init__(self, api_key: Optional[str], modelo: str):
        self.api_key = api_key
        self.modelo = modelo
        self._transport = None  # trocado por MockTransport nos testes

    def disponivel(self) -> bool:
        return bool(self.api_key)

    def _chamar(self, mensagens: List[Dict[str, str]], timeout: float,
                json_mode: bool) -> Tuple[str, int, int]:
        if not self.disponivel():
            raise ErroIA("sem_chave", "OPENAI_API_KEY não configurada")

        corpo: Dict[str, Any] = {"model": self.modelo, "messages": mensagens}
        if json_mode:
            corpo["response_format"] = {"type": "json_object"}

        try:
            with httpx.Client(timeout=timeout, transport=self._transport) as cliente:
                r = cliente.post(
                    URL,
                    json=corpo,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
        except httpx.TimeoutException as e:
            raise ErroIA("timeout", str(e))
        except httpx.HTTPError as e:
            raise ErroIA("http", str(e))

        if r.status_code >= 400:
            logger.error("OpenAI %s: %s", r.status_code, r.text[:300])
            raise ErroIA("http", f"status {r.status_code}")

        dados = r.json()
        conteudo = dados["choices"][0]["message"]["content"]
        uso = dados.get("usage") or {}
        return conteudo, int(uso.get("prompt_tokens") or 0), int(uso.get("completion_tokens") or 0)

    def completar_json(self, sistema: str, usuario: str,
                       timeout: float = 60.0) -> Tuple[Dict[str, Any], int, int]:
        conteudo, entrada, saida = self._chamar(
            [{"role": "system", "content": sistema}, {"role": "user", "content": usuario}],
            timeout=timeout, json_mode=True,
        )
        try:
            return json.loads(conteudo), entrada, saida
        except (json.JSONDecodeError, TypeError) as e:
            raise ErroIA("formato", str(e))

    def completar_texto(self, sistema: str, mensagens: List[Dict[str, str]],
                        timeout: float = 60.0) -> Tuple[str, int, int]:
        return self._chamar(
            [{"role": "system", "content": sistema}] + mensagens,
            timeout=timeout, json_mode=False,
        )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/unit/test_openai_client.py -q`
Expected: PASS — 8 passed

- [ ] **Step 5: Commit**

```bash
git add app/services/openai_client.py tests/unit/test_openai_client.py
git commit -m "feat(ia): cliente OpenAI isolado com erro tipado

Fronteira única com a API: todo o resto é testável sem rede, e o motivo
tipado do erro é o que garante não debitar crédito quando a IA falha."
```

---

## Task 5: Orquestração do diagnóstico

**Files:**
- Create: `app/repositories/ai_diagnostic_repository.py`
- Create: `app/services/ai_prompts.py`
- Create: `app/services/ai_diagnostic_service.py`
- Test: `tests/unit/test_ai_diagnostic_service.py`

**Interfaces:**
- Consumes: `AiSnapshotService.montar`, `OpenAiClient.completar_json/.completar_texto`, `ErroIA`, `AiCreditService.debitar/.saldo/.tem_saldo`, `SaldoInsuficiente`, `CUSTO_GERACAO`, `CUSTO_CHAT`.
- Produces:
  - `PeriodoVazio(Exception)`, `GeracaoEmAndamento(Exception)`, `LimiteDeMensagens(Exception)`
  - `TETO_MENSAGENS = 20`
  - `AiDiagnosticService(db, cliente, snapshot_svc, credito_svc)`
  - `.gerar(user_id: int, plano: str, inicio: date, fim: date) -> AiDiagnostic`
  - `.responder(user_id: int, plano: str, diagnostic_id: int, pergunta: str) -> AiDiagnosticMessage`

- [ ] **Step 1: Escrever o teste**

```python
# tests/unit/test_ai_diagnostic_service.py
"""
Orquestração do Diagnóstico IA.

Dois invariantes valem mais que tudo aqui:
  1. a sessão NUNCA fica em "gerando" — sempre termina em pronto ou erro;
  2. falha da IA NÃO debita crédito.
"""
from datetime import date
from types import SimpleNamespace

import pytest

from app.models.ai_diagnostic import STATUS_ERRO, STATUS_PRONTO
from app.services.ai_credit_service import CUSTO_CHAT, CUSTO_GERACAO, SaldoInsuficiente
from app.services.ai_diagnostic_service import (
    TETO_MENSAGENS, AiDiagnosticService, LimiteDeMensagens, PeriodoVazio,
)
from app.services.openai_client import ErroIA

RELATORIO = {
    "resumo_executivo": "Operação saudável.",
    "escalar": [], "pausar": [], "observar": [],
    "detalhamento": [], "numeros": {}, "proximos_passos": [],
    "perguntas_sugeridas": ["Por que a X está no vermelho?"],
}


class _FakeRepo:
    def __init__(self):
        self.sessoes = {}
        self.mensagens = []
        self._seq = 0

    def criar(self, user_id, inicio, fim, snapshot):
        self._seq += 1
        s = SimpleNamespace(
            id=self._seq, user_id=user_id, periodo_inicio=inicio, periodo_fim=fim,
            snapshot=snapshot, relatorio=None, status="gerando", erro_mensagem=None,
            modelo=None, tokens_entrada=None, tokens_saida=None, concluido_em=None,
        )
        self.sessoes[self._seq] = s
        return s

    def salvar(self, sessao):
        self.sessoes[sessao.id] = sessao
        return sessao

    def buscar(self, diagnostic_id, user_id):
        s = self.sessoes.get(diagnostic_id)
        return s if s and s.user_id == user_id else None

    def em_andamento(self, user_id):
        return next((s for s in self.sessoes.values()
                     if s.user_id == user_id and s.status == "gerando"), None)

    def adicionar_mensagem(self, diagnostic_id, papel, conteudo):
        m = SimpleNamespace(id=len(self.mensagens) + 1, diagnostic_id=diagnostic_id,
                            papel=papel, conteudo=conteudo)
        self.mensagens.append(m)
        return m

    def listar_mensagens(self, diagnostic_id):
        return [m for m in self.mensagens if m.diagnostic_id == diagnostic_id]

    def contar_mensagens_da_usuaria(self, diagnostic_id):
        return len([m for m in self.mensagens
                    if m.diagnostic_id == diagnostic_id and m.papel == "user"])


class _FakeCliente:
    def __init__(self, json_resposta=None, texto="resposta", erro=None):
        self._json = json_resposta if json_resposta is not None else RELATORIO
        self._texto = texto
        self._erro = erro
        self.chamadas = 0

    def disponivel(self):
        return True

    def completar_json(self, sistema, usuario, timeout=60.0):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return self._json, 100, 50

    def completar_texto(self, sistema, mensagens, timeout=60.0):
        self.chamadas += 1
        if self._erro:
            raise self._erro
        return self._texto, 10, 5


class _FakeSnapshot:
    def __init__(self, vazio=False):
        self._vazio = vazio

    def montar(self, user_id, inicio, fim):
        return {"periodo": {"inicio": str(inicio), "fim": str(fim)},
                "kpis": {}, "tops": {}, "campanhas": [], "tem_meta": False,
                "vazio": self._vazio}


class _FakeCredito:
    def __init__(self, saldo_inicial=200):
        self._saldo = saldo_inicial
        self.debitos = []

    def saldo(self, user_id, plano):
        return self._saldo

    def tem_saldo(self, user_id, plano, custo):
        return self._saldo >= custo

    def debitar(self, user_id, plano, tipo, creditos, diagnostic_id=None):
        if self._saldo < creditos:
            raise SaldoInsuficiente(self._saldo, creditos)
        self._saldo -= creditos
        self.debitos.append({"tipo": tipo, "creditos": creditos, "diag": diagnostic_id})
        return self._saldo


def _servico(cliente=None, snapshot=None, credito=None, repo=None):
    return AiDiagnosticService(
        repo=repo or _FakeRepo(),
        cliente=cliente or _FakeCliente(),
        snapshot_svc=snapshot or _FakeSnapshot(),
        credito_svc=credito or _FakeCredito(),
    )


P = (date(2026, 8, 1), date(2026, 8, 5))


def test_geracao_com_sucesso_fica_pronta_e_debita_10():
    credito = _FakeCredito()
    s = _servico(credito=credito)
    sessao = s.gerar(1, "pro", *P)
    assert sessao.status == STATUS_PRONTO
    assert sessao.relatorio == RELATORIO
    assert credito.debitos == [{"tipo": "geracao", "creditos": CUSTO_GERACAO, "diag": sessao.id}]


def test_falha_da_ia_marca_erro_e_nao_debita():
    credito = _FakeCredito()
    s = _servico(cliente=_FakeCliente(erro=ErroIA("timeout")), credito=credito)
    sessao = s.gerar(1, "pro", *P)
    assert sessao.status == STATUS_ERRO
    assert credito.debitos == []


def test_sessao_nunca_termina_em_gerando():
    for erro in (ErroIA("timeout"), ErroIA("http"), ErroIA("formato")):
        s = _servico(cliente=_FakeCliente(erro=erro))
        assert s.gerar(1, "pro", *P).status != "gerando"


def test_periodo_vazio_nem_chama_a_ia():
    cliente = _FakeCliente()
    s = _servico(cliente=cliente, snapshot=_FakeSnapshot(vazio=True))
    with pytest.raises(PeriodoVazio):
        s.gerar(1, "pro", *P)
    assert cliente.chamadas == 0


def test_sem_saldo_nem_chama_a_ia():
    cliente = _FakeCliente()
    s = _servico(cliente=cliente, credito=_FakeCredito(saldo_inicial=3))
    with pytest.raises(SaldoInsuficiente):
        s.gerar(1, "pro", *P)
    assert cliente.chamadas == 0


def test_chat_debita_1_credito_e_grava_as_duas_pontas():
    repo = _FakeRepo()
    credito = _FakeCredito()
    s = _servico(repo=repo, credito=credito)
    sessao = s.gerar(1, "pro", *P)
    credito.debitos.clear()

    s.responder(1, "pro", sessao.id, "por quê?")
    papeis = [m.papel for m in repo.listar_mensagens(sessao.id)]
    assert papeis == ["user", "assistant"]
    assert credito.debitos == [{"tipo": "chat", "creditos": CUSTO_CHAT, "diag": sessao.id}]


def test_chat_respeita_o_teto_de_mensagens():
    repo = _FakeRepo()
    s = _servico(repo=repo)
    sessao = s.gerar(1, "pro", *P)
    for i in range(TETO_MENSAGENS):
        repo.adicionar_mensagem(sessao.id, "user", f"p{i}")
    with pytest.raises(LimiteDeMensagens):
        s.responder(1, "pro", sessao.id, "mais uma")


def test_chat_de_sessao_de_outra_usuaria_nao_responde():
    repo = _FakeRepo()
    s = _servico(repo=repo)
    sessao = s.gerar(1, "pro", *P)
    with pytest.raises(ValueError):
        s.responder(999, "pro", sessao.id, "oi")


def test_falha_no_chat_nao_debita():
    repo = _FakeRepo()
    credito = _FakeCredito()
    s = _servico(repo=repo, credito=credito)
    sessao = s.gerar(1, "pro", *P)
    credito.debitos.clear()
    s.cliente = _FakeCliente(erro=ErroIA("timeout"))
    with pytest.raises(ErroIA):
        s.responder(1, "pro", sessao.id, "por quê?")
    assert credito.debitos == []
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/test_ai_diagnostic_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.ai_diagnostic_service'`

- [ ] **Step 3: Escrever o repository**

```python
# app/repositories/ai_diagnostic_repository.py
"""Acesso às sessões de diagnóstico e suas mensagens."""
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_diagnostic import (
    STATUS_GERANDO, AiDiagnostic, AiDiagnosticMessage,
)


class AiDiagnosticRepository:
    def __init__(self, db: Session):
        self.db = db

    def criar(self, user_id: int, inicio: date, fim: date,
              snapshot: Dict[str, Any]) -> AiDiagnostic:
        sessao = AiDiagnostic(
            user_id=user_id, periodo_inicio=inicio, periodo_fim=fim,
            snapshot=snapshot, status=STATUS_GERANDO,
        )
        self.db.add(sessao)
        self.db.commit()
        self.db.refresh(sessao)
        return sessao

    def salvar(self, sessao: AiDiagnostic) -> AiDiagnostic:
        self.db.commit()
        self.db.refresh(sessao)
        return sessao

    def buscar(self, diagnostic_id: int, user_id: int) -> Optional[AiDiagnostic]:
        return (
            self.db.query(AiDiagnostic)
            .filter(AiDiagnostic.id == diagnostic_id, AiDiagnostic.user_id == user_id)
            .first()
        )

    def em_andamento(self, user_id: int) -> Optional[AiDiagnostic]:
        return (
            self.db.query(AiDiagnostic)
            .filter(AiDiagnostic.user_id == user_id, AiDiagnostic.status == STATUS_GERANDO)
            .first()
        )

    def listar(self, user_id: int, limite: int = 30) -> List[AiDiagnostic]:
        return (
            self.db.query(AiDiagnostic)
            .filter(AiDiagnostic.user_id == user_id)
            .order_by(AiDiagnostic.criado_em.desc())
            .limit(limite)
            .all()
        )

    def adicionar_mensagem(self, diagnostic_id: int, papel: str,
                           conteudo: str) -> AiDiagnosticMessage:
        m = AiDiagnosticMessage(diagnostic_id=diagnostic_id, papel=papel, conteudo=conteudo)
        self.db.add(m)
        self.db.commit()
        self.db.refresh(m)
        return m

    def listar_mensagens(self, diagnostic_id: int) -> List[AiDiagnosticMessage]:
        return (
            self.db.query(AiDiagnosticMessage)
            .filter(AiDiagnosticMessage.diagnostic_id == diagnostic_id)
            .order_by(AiDiagnosticMessage.criado_em.asc())
            .all()
        )

    def contar_mensagens_da_usuaria(self, diagnostic_id: int) -> int:
        return (
            self.db.query(AiDiagnosticMessage)
            .filter(
                AiDiagnosticMessage.diagnostic_id == diagnostic_id,
                AiDiagnosticMessage.papel == "user",
            )
            .count()
        )
```

- [ ] **Step 4: Escrever os prompts**

```python
# app/services/ai_prompts.py
"""
Prompts do Diagnóstico IA.

A instrução mais importante é a proibição de recalcular: os números chegam
prontos e classificados pelo backend. Num produto de dados, número alucinado
é falha fatal.
"""
import json
from typing import Any, Dict

SISTEMA_RELATORIO = """Você é analista de marketing de afiliados e escreve para \
afiliadas brasileiras, em português do Brasil, com tom direto e prático.

REGRAS INEGOCIÁVEIS:
1. Os números que você recebe são FATOS já calculados. NUNCA recalcule, some, \
divida ou estime nada. Se um número não está nos dados, não invente e não cite.
2. A classificação de cada campanha ("classificacao") já vem decidida: \
"healthy" = acima do ponto de equilíbrio, "warning" = perto do limite, \
"loss" = dando prejuízo, "unlinked" = sem vínculo com vendas. Não reclassifique.
3. O ponto de equilíbrio é ROAS 1,0 — abaixo disso a campanha perde dinheiro.
4. Se não houver campanhas nos dados, NÃO mencione campanhas em nenhuma seção; \
foque nos números gerais, canais, categorias e sub_ids.
5. Fale em reais (R$) e use os valores exatamente como vieram.

Responda SOMENTE com um JSON válido neste formato:
{
  "resumo_executivo": "2 a 3 frases sobre a saúde geral do período",
  "escalar": [{"nome": "...", "motivo": "...", "acao": "..."}],
  "pausar": [{"nome": "...", "motivo": "...", "perda": "..."}],
  "observar": [{"nome": "...", "motivo": "..."}],
  "detalhamento": [{"nome": "...", "diagnostico": "...", "custo": "..."}],
  "numeros": {"destaque": "...", "atencao": "..."},
  "proximos_passos": ["...", "...", "..."],
  "perguntas_sugeridas": ["...", "...", "..."]
}
As "perguntas_sugeridas" devem ser 3 perguntas curtas que a afiliada faria \
sobre ESTE relatório, citando nomes reais que aparecem nos dados."""

SISTEMA_CHAT = """Você é analista de marketing de afiliados conversando com uma \
afiliada brasileira sobre um diagnóstico que você mesmo escreveu.

REGRAS INEGOCIÁVEIS:
1. Responda APENAS com base nos dados do diagnóstico abaixo. Eles estão \
congelados: são o retrato do período analisado.
2. NUNCA recalcule nem invente número. Se a resposta não está nos dados, diga \
que aquilo não faz parte deste diagnóstico e sugira gerar um novo.
3. Seja direto e curto: 2 a 4 frases, salvo se pedirem detalhe.
4. Português do Brasil, tom prático, sem jargão desnecessário."""


def montar_entrada_relatorio(snapshot: Dict[str, Any]) -> str:
    return (
        "Dados do período (já calculados e classificados):\n"
        + json.dumps(snapshot, ensure_ascii=False, indent=2)
    )


def montar_contexto_chat(snapshot: Dict[str, Any], relatorio: Dict[str, Any]) -> str:
    return (
        SISTEMA_CHAT
        + "\n\nDADOS CONGELADOS DO PERÍODO:\n"
        + json.dumps(snapshot, ensure_ascii=False)
        + "\n\nRELATÓRIO QUE VOCÊ ESCREVEU:\n"
        + json.dumps(relatorio, ensure_ascii=False)
    )
```

- [ ] **Step 5: Escrever o serviço**

```python
# app/services/ai_diagnostic_service.py
"""
Orquestra o Diagnóstico IA: saldo, snapshot, chamada, persistência.

Síncrono de propósito. A chamada leva ~5-15s, o que cabe numa requisição — e
fila já provou perder trabalho em silêncio neste projeto. Numa feature que
debita crédito por clique, sumir calado depois de cobrar é o pior resultado.

Dois invariantes:
  1. a sessão nunca fica em "gerando";
  2. crédito só é debitado quando a análise chega.
"""
import logging
from datetime import date, datetime, timezone

from app.models.ai_diagnostic import (
    PAPEL_IA, PAPEL_USUARIA, STATUS_ERRO, STATUS_PRONTO,
)
from app.services.ai_credit_service import CUSTO_CHAT, CUSTO_GERACAO
from app.services.ai_prompts import (
    SISTEMA_RELATORIO, montar_contexto_chat, montar_entrada_relatorio,
)
from app.services.openai_client import ErroIA

logger = logging.getLogger(__name__)

TETO_MENSAGENS = 20

MENSAGEM_POR_MOTIVO = {
    "sem_chave": "A análise por IA está indisponível no momento.",
    "timeout": "A análise demorou mais que o esperado. Tente de novo.",
    "http": "Não conseguimos falar com o serviço de IA agora. Tente de novo.",
    "formato": "A análise voltou incompleta. Tente de novo.",
}


class PeriodoVazio(Exception):
    pass


class GeracaoEmAndamento(Exception):
    pass


class LimiteDeMensagens(Exception):
    pass


class AiDiagnosticService:
    def __init__(self, repo, cliente, snapshot_svc, credito_svc):
        self.repo = repo
        self.cliente = cliente
        self.snapshot_svc = snapshot_svc
        self.credito_svc = credito_svc

    def gerar(self, user_id: int, plano: str, inicio: date, fim: date):
        em_curso = self.repo.em_andamento(user_id)
        if em_curso:
            raise GeracaoEmAndamento(em_curso.id)

        # Sem saldo: nem monta snapshot, nem chama a IA.
        if not self.credito_svc.tem_saldo(user_id, plano, CUSTO_GERACAO):
            from app.services.ai_credit_service import SaldoInsuficiente
            raise SaldoInsuficiente(self.credito_svc.saldo(user_id, plano), CUSTO_GERACAO)

        snapshot = self.snapshot_svc.montar(user_id, inicio, fim)
        if snapshot.get("vazio"):
            # Não gasta crédito para dizer "não há dados".
            raise PeriodoVazio()

        sessao = self.repo.criar(user_id, inicio, fim, snapshot)
        try:
            relatorio, entrada, saida = self.cliente.completar_json(
                SISTEMA_RELATORIO, montar_entrada_relatorio(snapshot)
            )
        except ErroIA as e:
            logger.warning("Diagnóstico %s falhou (%s)", sessao.id, e.motivo)
            sessao.status = STATUS_ERRO
            sessao.erro_mensagem = MENSAGEM_POR_MOTIVO.get(e.motivo, "Falha ao gerar a análise.")
            sessao.concluido_em = datetime.now(timezone.utc)
            return self.repo.salvar(sessao)

        sessao.relatorio = relatorio
        sessao.status = STATUS_PRONTO
        sessao.modelo = getattr(self.cliente, "modelo", None)
        sessao.tokens_entrada = entrada
        sessao.tokens_saida = saida
        sessao.concluido_em = datetime.now(timezone.utc)
        self.repo.salvar(sessao)

        # Débito só aqui: a análise chegou.
        self.credito_svc.debitar(user_id, plano, "geracao", CUSTO_GERACAO,
                                 diagnostic_id=sessao.id)
        return sessao

    def responder(self, user_id: int, plano: str, diagnostic_id: int, pergunta: str):
        sessao = self.repo.buscar(diagnostic_id, user_id)
        if not sessao:
            raise ValueError("Diagnóstico não encontrado")
        if sessao.status != STATUS_PRONTO:
            raise ValueError("Diagnóstico ainda não está pronto")
        if self.repo.contar_mensagens_da_usuaria(diagnostic_id) >= TETO_MENSAGENS:
            raise LimiteDeMensagens()
        if not self.credito_svc.tem_saldo(user_id, plano, CUSTO_CHAT):
            from app.services.ai_credit_service import SaldoInsuficiente
            raise SaldoInsuficiente(self.credito_svc.saldo(user_id, plano), CUSTO_CHAT)

        historico = [
            {"role": m.papel, "content": m.conteudo}
            for m in self.repo.listar_mensagens(diagnostic_id)
        ]
        historico.append({"role": "user", "content": pergunta})

        # Chamada ANTES de gravar: falha não deixa pergunta órfã nem debita.
        texto, _, _ = self.cliente.completar_texto(
            montar_contexto_chat(sessao.snapshot, sessao.relatorio), historico
        )

        self.repo.adicionar_mensagem(diagnostic_id, PAPEL_USUARIA, pergunta)
        resposta = self.repo.adicionar_mensagem(diagnostic_id, PAPEL_IA, texto)
        self.credito_svc.debitar(user_id, plano, "chat", CUSTO_CHAT,
                                 diagnostic_id=diagnostic_id)
        return resposta
```

- [ ] **Step 6: Rodar e ver passar**

Run: `python -m pytest tests/unit/test_ai_diagnostic_service.py -q`
Expected: PASS — 9 passed

- [ ] **Step 7: Commit**

```bash
git add app/repositories/ai_diagnostic_repository.py app/services/ai_prompts.py \
        app/services/ai_diagnostic_service.py tests/unit/test_ai_diagnostic_service.py
git commit -m "feat(ia): orquestração do diagnóstico com estado terminal garantido

Síncrono de propósito: fila já provou perder trabalho em silêncio aqui, e
esta feature debita crédito por clique. Falha da IA marca erro e não cobra."
```

---

## Task 6: Endpoints

**Files:**
- Create: `app/schemas/ai_diagnostic.py`
- Create: `app/api/v1/routes/ai_diagnostics.py`
- Modify: `app/api/v1/routes/__init__.py`
- Test: `tests/unit/test_ai_diagnostic_routes.py`

**Interfaces:**
- Produces os endpoints, todos sob `require_active_subscription` e `require_plan("pro")`:
  - `GET  /api/v1/ai-diagnostics/saldo` → `{"saldo": int, "cota": int, "custo_geracao": 10, "custo_chat": 1, "disponivel": bool}`
  - `POST /api/v1/ai-diagnostics` body `{"inicio": "YYYY-MM-DD", "fim": "YYYY-MM-DD"}` → sessão
  - `GET  /api/v1/ai-diagnostics` → histórico (lista resumida)
  - `GET  /api/v1/ai-diagnostics/{id}` → sessão + mensagens
  - `POST /api/v1/ai-diagnostics/{id}/mensagens` body `{"pergunta": "..."}` → mensagem da IA

- [ ] **Step 1: Escrever o teste**

```python
# tests/unit/test_ai_diagnostic_routes.py
"""Mapeamento de exceção → status HTTP nas rotas do Diagnóstico IA."""
import pytest
from fastapi import HTTPException

from app.api.v1.routes.ai_diagnostics import traduzir_erro
from app.services.ai_credit_service import SaldoInsuficiente
from app.services.ai_diagnostic_service import (
    GeracaoEmAndamento, LimiteDeMensagens, PeriodoVazio,
)
from app.services.openai_client import ErroIA


def test_sem_saldo_vira_402_com_saldo_no_corpo():
    e = traduzir_erro(SaldoInsuficiente(saldo=3, necessario=10))
    assert e.status_code == 402
    assert e.detail["saldo"] == 3
    assert e.detail["necessario"] == 10


def test_periodo_vazio_vira_422():
    assert traduzir_erro(PeriodoVazio()).status_code == 422


def test_geracao_em_andamento_vira_409():
    assert traduzir_erro(GeracaoEmAndamento(7)).status_code == 409


def test_limite_de_mensagens_vira_429():
    assert traduzir_erro(LimiteDeMensagens()).status_code == 429


def test_ia_indisponivel_vira_503():
    assert traduzir_erro(ErroIA("sem_chave")).status_code == 503


def test_erro_desconhecido_nao_e_traduzido():
    assert traduzir_erro(RuntimeError("boom")) is None
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/unit/test_ai_diagnostic_routes.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api.v1.routes.ai_diagnostics'`

- [ ] **Step 3: Escrever os schemas**

```python
# app/schemas/ai_diagnostic.py
"""Contratos das rotas do Diagnóstico IA."""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class GerarDiagnosticoRequest(BaseModel):
    inicio: date
    fim: date


class PerguntaRequest(BaseModel):
    pergunta: str = Field(min_length=1, max_length=1000)


class MensagemResponse(BaseModel):
    id: int
    papel: str
    conteudo: str
    criado_em: Optional[datetime] = None


class DiagnosticoResumo(BaseModel):
    id: int
    periodo_inicio: date
    periodo_fim: date
    status: str
    criado_em: Optional[datetime] = None


class DiagnosticoResponse(BaseModel):
    id: int
    periodo_inicio: date
    periodo_fim: date
    status: str
    erro_mensagem: Optional[str] = None
    relatorio: Optional[Dict[str, Any]] = None
    snapshot: Optional[Dict[str, Any]] = None
    criado_em: Optional[datetime] = None
    mensagens: List[MensagemResponse] = []


class SaldoResponse(BaseModel):
    saldo: int
    cota: int
    custo_geracao: int
    custo_chat: int
    disponivel: bool
```

- [ ] **Step 4: Escrever as rotas**

```python
# app/api/v1/routes/ai_diagnostics.py
"""Endpoints do Diagnóstico IA."""
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import require_active_subscription, require_plan
from app.core.config import settings
from app.core.plans import normalize_plan
from app.db.session import get_db
from app.models.user import User
from app.repositories.ai_credit_repository import AiCreditRepository
from app.repositories.ai_diagnostic_repository import AiDiagnosticRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.ai_diagnostic import (
    DiagnosticoResponse, DiagnosticoResumo, GerarDiagnosticoRequest,
    MensagemResponse, PerguntaRequest, SaldoResponse,
)
from app.services.ai_credit_service import (
    CUSTO_CHAT, CUSTO_GERACAO, AiCreditService, SaldoInsuficiente,
)
from app.services.ai_diagnostic_service import (
    AiDiagnosticService, GeracaoEmAndamento, LimiteDeMensagens, PeriodoVazio,
)
from app.services.ai_snapshot_service import AiSnapshotService
from app.services.openai_client import ErroIA, OpenAiClient

logger = logging.getLogger(__name__)

router = APIRouter(tags=["diagnostico-ia"])


def traduzir_erro(exc: Exception) -> Optional[HTTPException]:
    """Exceção de domínio → HTTP. Devolve None quando não é erro conhecido."""
    if isinstance(exc, SaldoInsuficiente):
        return HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail={"code": "SEM_CREDITOS", "saldo": exc.saldo,
                    "necessario": exc.necessario,
                    "message": "Seus créditos de IA acabaram neste mês."},
        )
    if isinstance(exc, PeriodoVazio):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Não há dados no período escolhido. Escolha outro período.",
        )
    if isinstance(exc, GeracaoEmAndamento):
        return HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Já existe uma análise em andamento.",
        )
    if isinstance(exc, LimiteDeMensagens):
        return HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Esta conversa atingiu o limite. Gere um novo diagnóstico.",
        )
    if isinstance(exc, ErroIA):
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A análise por IA está indisponível no momento.",
        )
    return None


def _plano(db: Session, user_id: int) -> str:
    sub = SubscriptionRepository(db).get_by_user_id(user_id)
    return normalize_plan(sub.plan if sub else None)


def _credito(db: Session) -> AiCreditService:
    return AiCreditService(AiCreditRepository(db))


def _servico(db: Session) -> AiDiagnosticService:
    return AiDiagnosticService(
        repo=AiDiagnosticRepository(db),
        cliente=OpenAiClient(settings.OPENAI_API_KEY, settings.OPENAI_MODEL),
        snapshot_svc=AiSnapshotService(db),
        credito_svc=_credito(db),
    )


def _montar_resposta(sessao, mensagens=None) -> DiagnosticoResponse:
    return DiagnosticoResponse(
        id=sessao.id,
        periodo_inicio=sessao.periodo_inicio,
        periodo_fim=sessao.periodo_fim,
        status=sessao.status,
        erro_mensagem=sessao.erro_mensagem,
        relatorio=sessao.relatorio,
        snapshot=sessao.snapshot,
        criado_em=sessao.criado_em,
        mensagens=[
            MensagemResponse(id=m.id, papel=m.papel, conteudo=m.conteudo,
                             criado_em=m.criado_em)
            for m in (mensagens or [])
        ],
    )


@router.get("/saldo", response_model=SaldoResponse)
def saldo(
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    plano = _plano(db, current_user.id)
    servico = _credito(db)
    return SaldoResponse(
        saldo=servico.saldo(current_user.id, plano),
        cota=servico.cota(plano),
        custo_geracao=CUSTO_GERACAO,
        custo_chat=CUSTO_CHAT,
        disponivel=bool(settings.OPENAI_API_KEY),
    )


@router.post("", response_model=DiagnosticoResponse, status_code=status.HTTP_201_CREATED)
def gerar(
    payload: GerarDiagnosticoRequest,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    if payload.fim < payload.inicio:
        raise HTTPException(status_code=400, detail="Período inválido.")
    try:
        sessao = _servico(db).gerar(
            current_user.id, _plano(db, current_user.id), payload.inicio, payload.fim
        )
    except Exception as exc:
        traduzido = traduzir_erro(exc)
        if traduzido:
            raise traduzido
        logger.exception("Falha inesperada ao gerar diagnóstico")
        raise HTTPException(status_code=500, detail="Erro ao gerar a análise.")
    return _montar_resposta(sessao)


@router.get("", response_model=List[DiagnosticoResumo])
def listar(
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    return [
        DiagnosticoResumo(
            id=s.id, periodo_inicio=s.periodo_inicio, periodo_fim=s.periodo_fim,
            status=s.status, criado_em=s.criado_em,
        )
        for s in AiDiagnosticRepository(db).listar(current_user.id)
    ]


@router.get("/{diagnostic_id}", response_model=DiagnosticoResponse)
def detalhar(
    diagnostic_id: int,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    repo = AiDiagnosticRepository(db)
    sessao = repo.buscar(diagnostic_id, current_user.id)
    if not sessao:
        raise HTTPException(status_code=404, detail="Diagnóstico não encontrado")
    return _montar_resposta(sessao, repo.listar_mensagens(diagnostic_id))


@router.post("/{diagnostic_id}/mensagens", response_model=MensagemResponse)
def responder(
    diagnostic_id: int,
    payload: PerguntaRequest,
    current_user: User = Depends(require_plan("pro")),
    db: Session = Depends(get_db),
):
    try:
        m = _servico(db).responder(
            current_user.id, _plano(db, current_user.id), diagnostic_id, payload.pergunta
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        traduzido = traduzir_erro(exc)
        if traduzido:
            raise traduzido
        logger.exception("Falha inesperada no chat do diagnóstico")
        raise HTTPException(status_code=500, detail="Erro ao responder.")
    return MensagemResponse(id=m.id, papel=m.papel, conteudo=m.conteudo,
                            criado_em=getattr(m, "criado_em", None))
```

- [ ] **Step 5: Registrar o router**

Em `app/api/v1/routes/__init__.py`, acrescentar `ai_diagnostics` ao import e:

```python
router.include_router(ai_diagnostics.router, prefix="/ai-diagnostics")
```

- [ ] **Step 6: Rodar e ver passar**

Run: `python -m pytest tests/unit/test_ai_diagnostic_routes.py -q && python -c "import app.main; print('ok')"`
Expected: PASS — 6 passed, e `ok`

- [ ] **Step 7: Commit**

```bash
git add app/schemas/ai_diagnostic.py app/api/v1/routes/ai_diagnostics.py \
        app/api/v1/routes/__init__.py tests/unit/test_ai_diagnostic_routes.py
git commit -m "feat(ia): endpoints do Diagnóstico IA"
```

---

## Task 7: Aplicar migration em homologação e validar ponta a ponta

**Files:**
- Nenhum arquivo novo. Executa a migration e um smoke real contra a OpenAI.

- [ ] **Step 1: Conferir que o `.env` aponta para homologação**

```bash
cd marketdash-backend && source .venv312/bin/activate
python -c "
from app.core.config import settings
assert 'ytjpdvjuxtvxacredekk' in settings.DATABASE_URL, 'ABORTAR: não é homologação'
print('ok, banco de homologação')"
```
Expected: `ok, banco de homologação`

- [ ] **Step 2: Aplicar a migration**

```bash
python -c "
from sqlalchemy import create_engine, text
from app.core.config import settings
assert 'ytjpdvjuxtvxacredekk' in settings.DATABASE_URL
sql = open('migrations/043_diagnostico_ia.sql').read()
eng = create_engine(settings.DATABASE_URL)
with eng.begin() as c:
    c.execute(text(sql))
print('migration 043 aplicada')"
```
Expected: `migration 043 aplicada`

- [ ] **Step 3: Conferir as tabelas**

```bash
python -c "
from sqlalchemy import create_engine, inspect
from app.core.config import settings
insp = inspect(create_engine(settings.DATABASE_URL))
for t in ('ai_diagnostics','ai_diagnostic_messages','ai_credit_ledger'):
    assert insp.has_table(t), t
print('3 tabelas ok')"
```
Expected: `3 tabelas ok`

- [ ] **Step 4: Smoke real contra a OpenAI (uma chamada, ~1 centavo)**

```bash
python -c "
from app.core.config import settings
from app.services.openai_client import OpenAiClient
from app.services.ai_prompts import SISTEMA_RELATORIO, montar_entrada_relatorio
snap = {'periodo':{'inicio':'2026-08-01','fim':'2026-08-05'},
        'kpis':{'comissao_liquida':1200.0,'receita':8000.0,'gasto':500.0,'lucro':700.0,'pedidos':42},
        'tops':{'canal':[{'nome':'WhatsApp','comissao':800.0,'pedidos':30}],'categoria':[],'sub_id':[]},
        'campanhas':[{'nome':'kit_cozinha','classificacao':'loss','roas':0.4,'gasto':300.0,
                      'comissao_liquida':120.0,'lucro':-180.0,'pedidos':5,'cliques':200,
                      'ativa':True,'vinculada':True}],
        'tem_meta':True,'vazio':False}
c = OpenAiClient(settings.OPENAI_API_KEY, settings.OPENAI_MODEL)
rel, ent, sai = c.completar_json(SISTEMA_RELATORIO, montar_entrada_relatorio(snap))
assert 'resumo_executivo' in rel and isinstance(rel.get('pausar'), list)
assert rel['perguntas_sugeridas'], 'faltaram perguntas sugeridas'
print('resumo:', rel['resumo_executivo'][:120])
print('pausar:', [p.get('nome') for p in rel['pausar']])
print('tokens:', ent, sai)"
```
Expected: resumo em português citando a campanha em prejuízo, `pausar` contendo `kit_cozinha`, tokens > 0

- [ ] **Step 5: Commit e deploy para homologação**

```bash
git push origin develop
gh run list --limit 1
```
Expected: dispara **apenas** `Deploy to Homologation`. Se aparecer `Deploy to Production`, PARE — algo foi para `main`.

---

## Task 8: Frontend — serviço e tipos

**Files:**
- Create: `marketdash-frontend/src/services/ai-diagnostic.service.ts`

**Interfaces:**
- Produces: `fetchSaldoIA()`, `gerarDiagnostico(inicio, fim)`, `listarDiagnosticos()`, `buscarDiagnostico(id)`, `enviarPergunta(id, pergunta)` e os tipos `SaldoIA`, `Diagnostico`, `DiagnosticoResumo`, `MensagemIA`, `RelatorioIA`.

- [ ] **Step 1: Escrever o serviço**

```typescript
// src/services/ai-diagnostic.service.ts
import { fetchWithAuth } from "@/core/config/api.config";

const base = () => "/api/v1/ai-diagnostics";

async function json<T>(r: Response): Promise<T> {
  if (!r.ok) {
    const corpo = await r.json().catch(() => ({}));
    const erro = new Error(
      typeof corpo?.detail === "string" ? corpo.detail : "Erro na requisição",
    ) as Error & { status?: number; detail?: unknown };
    erro.status = r.status;
    erro.detail = corpo?.detail;
    throw erro;
  }
  return r.json() as Promise<T>;
}

export type SaldoIA = {
  saldo: number;
  cota: number;
  custo_geracao: number;
  custo_chat: number;
  disponivel: boolean;
};

export type MensagemIA = {
  id: number;
  papel: "user" | "assistant";
  conteudo: string;
  criado_em?: string | null;
};

export type RelatorioIA = {
  resumo_executivo: string;
  escalar: { nome: string; motivo: string; acao: string }[];
  pausar: { nome: string; motivo: string; perda: string }[];
  observar: { nome: string; motivo: string }[];
  detalhamento: { nome: string; diagnostico: string; custo: string }[];
  numeros: { destaque?: string; atencao?: string };
  proximos_passos: string[];
  perguntas_sugeridas: string[];
};

export type Diagnostico = {
  id: number;
  periodo_inicio: string;
  periodo_fim: string;
  status: "gerando" | "pronto" | "erro";
  erro_mensagem?: string | null;
  relatorio?: RelatorioIA | null;
  snapshot?: Record<string, unknown> | null;
  criado_em?: string | null;
  mensagens: MensagemIA[];
};

export type DiagnosticoResumo = Omit<Diagnostico, "relatorio" | "snapshot" | "mensagens">;

export async function fetchSaldoIA() {
  return json<SaldoIA>(await fetchWithAuth(`${base()}/saldo`));
}

export async function gerarDiagnostico(inicio: string, fim: string) {
  return json<Diagnostico>(
    await fetchWithAuth(base(), {
      method: "POST",
      body: JSON.stringify({ inicio, fim }),
    }),
  );
}

export async function listarDiagnosticos() {
  return json<DiagnosticoResumo[]>(await fetchWithAuth(base()));
}

export async function buscarDiagnostico(id: number) {
  return json<Diagnostico>(await fetchWithAuth(`${base()}/${id}`));
}

export async function enviarPergunta(id: number, pergunta: string) {
  return json<MensagemIA>(
    await fetchWithAuth(`${base()}/${id}/mensagens`, {
      method: "POST",
      body: JSON.stringify({ pergunta }),
    }),
  );
}
```

- [ ] **Step 2: Type check**

Run: `cd marketdash-frontend && npx tsc --noEmit`
Expected: sem erros

- [ ] **Step 3: Commit**

```bash
git add src/services/ai-diagnostic.service.ts
git commit -m "feat(ia): serviço e tipos do Diagnóstico IA"
```

---

## Task 9: Frontend — relatório e impressão

**Files:**
- Create: `marketdash-frontend/src/features/diagnostico/components/RelatorioDiagnostico.tsx`
- Create: `marketdash-frontend/src/features/diagnostico/print.css`

**Interfaces:**
- Consumes: `RelatorioIA`, `Diagnostico` (Task 8).
- Produces: `<RelatorioDiagnostico diagnostico={Diagnostico} />`

- [ ] **Step 1: Escrever o CSS de impressão**

```css
/* src/features/diagnostico/print.css
   PDF sai por window.print(): o relatório já está renderizado, então o
   arquivo é idêntico ao que a aluna vê. Evita ~100 MB de WeasyPrint no
   container do backend. */
@media print {
  body * { visibility: hidden; }
  #relatorio-diagnostico,
  #relatorio-diagnostico * { visibility: visible; }
  #relatorio-diagnostico {
    position: absolute;
    left: 0;
    top: 0;
    width: 100%;
    padding: 24px;
    color: #000;
    background: #fff;
  }
  .nao-imprimir { display: none !important; }
  #relatorio-diagnostico .bloco { break-inside: avoid; }
}
```

- [ ] **Step 2: Escrever o componente**

```tsx
// src/features/diagnostico/components/RelatorioDiagnostico.tsx
import { Printer } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Diagnostico } from "@/services/ai-diagnostic.service";
import "../print.css";

function Bloco({
  titulo,
  cor,
  itens,
}: {
  titulo: string;
  cor: string;
  itens: { nome: string; texto: string }[];
}) {
  if (itens.length === 0) return null;
  return (
    <Card className="bloco">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <span className={`inline-block h-2.5 w-2.5 rounded-full ${cor}`} />
          {titulo}
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {itens.map((i) => (
          <div key={i.nome} className="border-b border-border/50 pb-2 last:border-0">
            <p className="font-medium">{i.nome}</p>
            <p className="text-sm text-muted-foreground">{i.texto}</p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}

export function RelatorioDiagnostico({ diagnostico }: { diagnostico: Diagnostico }) {
  const r = diagnostico.relatorio;
  if (!r) return null;

  const periodo = `${new Date(diagnostico.periodo_inicio).toLocaleDateString("pt-BR")} a ${new Date(
    diagnostico.periodo_fim,
  ).toLocaleDateString("pt-BR")}`;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between nao-imprimir">
        <Badge variant="outline">{periodo}</Badge>
        <Button size="sm" variant="outline" onClick={() => window.print()}>
          <Printer className="mr-1.5 h-4 w-4" />
          Salvar PDF
        </Button>
      </div>

      <div id="relatorio-diagnostico" className="space-y-4">
        <Card className="bloco">
          <CardHeader className="pb-3">
            <CardTitle className="text-base">Resumo</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="leading-relaxed">{r.resumo_executivo}</p>
          </CardContent>
        </Card>

        <Bloco
          titulo="Escalar"
          cor="bg-emerald-500"
          itens={r.escalar.map((i) => ({ nome: i.nome, texto: `${i.motivo} — ${i.acao}` }))}
        />
        <Bloco
          titulo="Pausar"
          cor="bg-destructive"
          itens={r.pausar.map((i) => ({ nome: i.nome, texto: `${i.motivo} — ${i.perda}` }))}
        />
        <Bloco
          titulo="Observar"
          cor="bg-amber-500"
          itens={r.observar.map((i) => ({ nome: i.nome, texto: i.motivo }))}
        />
        <Bloco
          titulo="Detalhamento"
          cor="bg-sky-500"
          itens={r.detalhamento.map((i) => ({
            nome: i.nome,
            texto: `${i.diagnostico} — ${i.custo}`,
          }))}
        />

        {(r.numeros?.destaque || r.numeros?.atencao) && (
          <Card className="bloco">
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Números do período</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              {r.numeros.destaque && <p>✓ {r.numeros.destaque}</p>}
              {r.numeros.atencao && <p>⚠ {r.numeros.atencao}</p>}
            </CardContent>
          </Card>
        )}

        {r.proximos_passos.length > 0 && (
          <Card className="bloco">
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Próximos passos</CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="list-decimal space-y-1 pl-5 text-sm">
                {r.proximos_passos.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ol>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Type check e commit**

Run: `npx tsc --noEmit`
Expected: sem erros

```bash
git add src/features/diagnostico/
git commit -m "feat(ia): relatório do diagnóstico com exportação em PDF via print"
```

---

## Task 10: Frontend — chat

**Files:**
- Create: `marketdash-frontend/src/features/diagnostico/components/ChatDiagnostico.tsx`

**Interfaces:**
- Consumes: `enviarPergunta`, `MensagemIA`, `Diagnostico` (Task 8).
- Produces: `<ChatDiagnostico diagnostico={Diagnostico} onCreditoGasto={() => void} />`

- [ ] **Step 1: Escrever o componente**

```tsx
// src/features/diagnostico/components/ChatDiagnostico.tsx
import { useState } from "react";
import { Loader2, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  enviarPergunta,
  type Diagnostico,
  type MensagemIA,
} from "@/services/ai-diagnostic.service";

const TETO_MENSAGENS = 20;

export function ChatDiagnostico({
  diagnostico,
  onCreditoGasto,
}: {
  diagnostico: Diagnostico;
  onCreditoGasto: () => void;
}) {
  const [mensagens, setMensagens] = useState<MensagemIA[]>(diagnostico.mensagens || []);
  const [texto, setTexto] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);

  const perguntasDaUsuaria = mensagens.filter((m) => m.papel === "user").length;
  const noLimite = perguntasDaUsuaria >= TETO_MENSAGENS;
  const sugeridas = diagnostico.relatorio?.perguntas_sugeridas ?? [];

  const perguntar = async (pergunta: string) => {
    const limpo = pergunta.trim();
    if (!limpo || enviando || noLimite) return;
    setEnviando(true);
    setErro(null);
    const provisoria: MensagemIA = {
      id: Date.now(),
      papel: "user",
      conteudo: limpo,
    };
    setMensagens((m) => [...m, provisoria]);
    setTexto("");
    try {
      const resposta = await enviarPergunta(diagnostico.id, limpo);
      setMensagens((m) => [...m, resposta]);
      onCreditoGasto();
    } catch (e) {
      // desfaz a pergunta otimista: ela não foi gravada no servidor
      setMensagens((m) => m.filter((x) => x.id !== provisoria.id));
      setErro(e instanceof Error ? e.message : "Não foi possível responder agora.");
    } finally {
      setEnviando(false);
    }
  };

  return (
    <Card className="nao-imprimir">
      <CardHeader className="pb-3">
        <CardTitle className="text-base">Perguntas sobre esta análise</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {mensagens.length === 0 && sugeridas.length > 0 && (
          <div className="flex flex-wrap gap-2">
            {sugeridas.map((p) => (
              <Button key={p} size="sm" variant="outline" onClick={() => void perguntar(p)}>
                {p}
              </Button>
            ))}
          </div>
        )}

        <div className="space-y-3">
          {mensagens.map((m) => (
            <div
              key={m.id}
              className={m.papel === "user" ? "flex justify-end" : "flex justify-start"}
            >
              <div
                className={`max-w-[85%] rounded-lg px-3 py-2 text-sm ${
                  m.papel === "user" ? "bg-primary text-primary-foreground" : "bg-muted"
                }`}
              >
                {m.conteudo}
              </div>
            </div>
          ))}
          {enviando && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              Analisando…
            </div>
          )}
        </div>

        {erro && <p className="text-sm text-destructive">{erro}</p>}

        {noLimite ? (
          <p className="text-sm text-muted-foreground">
            Esta conversa atingiu o limite de {TETO_MENSAGENS} perguntas. Gere um novo
            diagnóstico para continuar.
          </p>
        ) : (
          <form
            className="flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void perguntar(texto);
            }}
          >
            <Input
              value={texto}
              onChange={(e) => setTexto(e.target.value)}
              placeholder="Pergunte sobre esta análise…"
              disabled={enviando}
            />
            <Button type="submit" size="icon" disabled={enviando || !texto.trim()}>
              <Send className="h-4 w-4" />
            </Button>
          </form>
        )}
        <p className="text-xs text-muted-foreground">
          {perguntasDaUsuaria} de {TETO_MENSAGENS} perguntas · 1 crédito por pergunta
        </p>
      </CardContent>
    </Card>
  );
}
```

- [ ] **Step 2: Type check e commit**

Run: `npx tsc --noEmit`
Expected: sem erros

```bash
git add src/features/diagnostico/components/ChatDiagnostico.tsx
git commit -m "feat(ia): chat do diagnóstico com perguntas sugeridas e teto de 20"
```

---

## Task 11: Frontend — tela, menu e rota

**Files:**
- Create: `marketdash-frontend/src/features/diagnostico/pages/DiagnosticoIA.tsx`
- Modify: `marketdash-frontend/src/app/routes/app-routes.tsx`
- Modify: `marketdash-frontend/src/components/dashboard/` (item de menu — seguir o padrão dos itens existentes, chave `diagnostico_ia`)

**Interfaces:**
- Consumes: tudo das Tasks 8-10.

- [ ] **Step 1: Escrever a tela**

```tsx
// src/features/diagnostico/pages/DiagnosticoIA.tsx
import { useEffect, useState } from "react";
import { Loader2, Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { ChatDiagnostico } from "../components/ChatDiagnostico";
import { RelatorioDiagnostico } from "../components/RelatorioDiagnostico";
import {
  buscarDiagnostico,
  fetchSaldoIA,
  gerarDiagnostico,
  listarDiagnosticos,
  type Diagnostico,
  type DiagnosticoResumo,
  type SaldoIA,
} from "@/services/ai-diagnostic.service";

const ATALHOS = [
  { label: "7 dias", dias: 7 },
  { label: "14 dias", dias: 14 },
  { label: "30 dias", dias: 30 },
];

/** Corta no fim do dia anterior em Brasília — mesmo critério do dashboard. */
function periodoDeDias(dias: number) {
  const agora = new Date();
  const fim = new Date(agora);
  fim.setDate(fim.getDate() - 1);
  const inicio = new Date(fim);
  inicio.setDate(inicio.getDate() - (dias - 1));
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  return { inicio: iso(inicio), fim: iso(fim) };
}

export default function DiagnosticoIAPage() {
  const [saldo, setSaldo] = useState<SaldoIA | null>(null);
  const [historico, setHistorico] = useState<DiagnosticoResumo[]>([]);
  const [atual, setAtual] = useState<Diagnostico | null>(null);
  const [gerando, setGerando] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const [dias, setDias] = useState(7);

  const recarregarSaldo = () => {
    void fetchSaldoIA().then(setSaldo).catch(() => undefined);
  };

  useEffect(() => {
    recarregarSaldo();
    void listarDiagnosticos().then(setHistorico).catch(() => undefined);
  }, []);

  const gerar = async () => {
    setGerando(true);
    setErro(null);
    try {
      const { inicio, fim } = periodoDeDias(dias);
      const sessao = await gerarDiagnostico(inicio, fim);
      setAtual(sessao);
      recarregarSaldo();
      setHistorico(await listarDiagnosticos());
      if (sessao.status === "erro") setErro(sessao.erro_mensagem || "Falha na análise.");
    } catch (e) {
      setErro(e instanceof Error ? e.message : "Não foi possível gerar a análise.");
    } finally {
      setGerando(false);
    }
  };

  const abrir = async (id: number) => {
    setErro(null);
    try {
      setAtual(await buscarDiagnostico(id));
    } catch {
      setErro("Não foi possível abrir este diagnóstico.");
    }
  };

  const semCredito = !!saldo && saldo.saldo < (saldo.custo_geracao ?? 10);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold">Diagnóstico IA</h1>
          <p className="text-sm text-muted-foreground">
            Uma análise da sua operação: o que escalar, o que pausar e por quê.
          </p>
        </div>
        {saldo && (
          <Badge variant="outline" className="tabular-nums">
            {saldo.saldo} de {saldo.cota} créditos
          </Badge>
        )}
      </div>

      <Card className="nao-imprimir">
        <CardContent className="flex flex-wrap items-center gap-3 pt-6">
          {ATALHOS.map((a) => (
            <Button
              key={a.dias}
              size="sm"
              variant={dias === a.dias ? "default" : "outline"}
              onClick={() => setDias(a.dias)}
            >
              {a.label}
            </Button>
          ))}
          <Button onClick={() => void gerar()} disabled={gerando || semCredito}>
            {gerando ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                Analisando…
              </>
            ) : (
              <>
                <Sparkles className="mr-1.5 h-4 w-4" />
                Gerar análise ({saldo?.custo_geracao ?? 10} créditos)
              </>
            )}
          </Button>
          {semCredito && (
            <p className="text-sm text-muted-foreground">
              Seus créditos acabaram neste mês.{" "}
              <a className="underline" href="/dashboard/planos">
                Ver planos
              </a>
            </p>
          )}
        </CardContent>
      </Card>

      {erro && <p className="text-sm text-destructive nao-imprimir">{erro}</p>}

      {atual?.status === "pronto" && (
        <>
          <RelatorioDiagnostico diagnostico={atual} />
          <ChatDiagnostico diagnostico={atual} onCreditoGasto={recarregarSaldo} />
        </>
      )}

      {historico.length > 0 && (
        <Card className="nao-imprimir">
          <CardContent className="space-y-2 pt-6">
            <p className="text-sm font-medium">Análises anteriores</p>
            {historico.map((h) => (
              <button
                key={h.id}
                onClick={() => void abrir(h.id)}
                className="flex w-full items-center justify-between rounded-md px-2 py-1.5 text-left text-sm hover:bg-accent"
              >
                <span>
                  {new Date(h.periodo_inicio).toLocaleDateString("pt-BR")} a{" "}
                  {new Date(h.periodo_fim).toLocaleDateString("pt-BR")}
                </span>
                <Badge variant={h.status === "pronto" ? "outline" : "secondary"}>
                  {h.status === "pronto" ? "pronta" : h.status}
                </Badge>
              </button>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Registrar a rota**

Em `src/app/routes/app-routes.tsx`, seguir exatamente o padrão dos itens vizinhos (import lazy + `<Route>` dentro do bloco do dashboard), com o caminho `/dashboard/diagnostico-ia` apontando para `DiagnosticoIAPage`.

- [ ] **Step 3: Adicionar o item de menu**

No arquivo de navegação em `src/components/dashboard/`, acrescentar o item seguindo o padrão dos existentes, com a chave `diagnostico_ia` (a mesma usada em `core/plans.py`) para que o gating por plano funcione sozinho, ícone `Sparkles` e rótulo "Diagnóstico IA".

- [ ] **Step 4: Type check, lint e build**

Run:
```bash
npx tsc --noEmit && npm run lint 2>&1 | tail -3 && npm run build 2>&1 | tail -3
```
Expected: tsc limpo; lint sem erros novos (baseline: 3 erros pré-existentes); build `✓ built`

- [ ] **Step 5: Commit e deploy**

```bash
git add src/features/diagnostico src/app/routes src/components/dashboard
git commit -m "feat(ia): tela do Diagnóstico IA com histórico, relatório e chat"
git push origin develop
```
Expected: dispara **apenas** `Deploy to Homologation`

---

## Task 12: Validação em homologação

- [ ] **Step 1: Conferir que os dois deploys ficaram verdes**

```bash
gh run list --limit 2 --json name,headBranch,conclusion
```
Expected: `Deploy to Homologation` com `success` nos dois repositórios. Nenhum `Deploy to Production`.

- [ ] **Step 2: Smoke da API**

```bash
curl -s -o /dev/null -w "%{http_code}\n" https://api.hml.marketdash.com.br/health
```
Expected: `200`

- [ ] **Step 3: Checklist manual em `hml.marketdash.com.br`**

- [ ] Menu "Diagnóstico IA" aparece para conta Pro e **não** aparece para Essencial
- [ ] Gerar análise devolve relatório em português citando campanhas reais
- [ ] Saldo cai de 200 para 190 depois de uma geração
- [ ] Clicar numa pergunta sugerida devolve resposta e saldo cai 1
- [ ] "Salvar PDF" abre a impressão só com o relatório, sem menu nem botões
- [ ] Reabrir do histórico mostra relatório e conversa
- [ ] Período sem dados devolve aviso e **não** consome crédito

- [ ] **Step 4: Conferir o extrato no banco de homologação**

```bash
cd marketdash-backend && source .venv312/bin/activate && python -c "
from sqlalchemy import create_engine, text
from app.core.config import settings
assert 'ytjpdvjuxtvxacredekk' in settings.DATABASE_URL
with create_engine(settings.DATABASE_URL).connect() as c:
    for r in c.execute(text('''
        select l.tipo, l.creditos, l.saldo_apos, d.status
        from ai_credit_ledger l left join ai_diagnostics d on d.id = l.diagnostic_id
        order by l.id desc limit 10''')):
        print(r)"
```
Expected: só linhas cujo diagnóstico está `pronto` — nenhuma associada a `erro`

---

## Auto-revisão

**Cobertura da spec:** §1 tela (T11) · §2 regra de ouro (T3 snapshot + T5 prompts) · §3 escopo com e sem Meta (T3) · §4.1 síncrono e estado terminal (T5) · §4.2 modelo de dados (T1) · §4.3 fluxo (T5) · §4.4 camadas (T2-T6) · §5 créditos (T2, T5) · §6 relatório e PDF (T9) · §7 chat (T10) · §8 erros (T5, T6) · §9 acesso (T2 plans.py, T11 menu) · §10 testes (todas as tasks) · §11 fora do escopo respeitado.

**Placeholders:** nenhum "TBD"/"TODO". Todos os passos de código têm o código.

**Consistência de tipos:** `AiCreditService.debitar(user_id, plano, tipo, creditos, diagnostic_id)` idêntico no fake do teste, no serviço e nas chamadas da T5. `snapshot["vazio"]` produzido na T3 e consumido na T5. `perguntas_sugeridas` produzido no prompt (T5), tipado na T8 e consumido na T10. `STATUS_PRONTO`/`STATUS_ERRO` definidos na T1 e usados nas T5/T6. Chave de menu `diagnostico_ia` igual em `plans.py` (T2) e no frontend (T11).
