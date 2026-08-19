# Contexto — MarketDash Backend

> **Estado atual do repositório.** Sobrescreva as seções ao mudarem — o
> histórico vive em `DIARIO.md`. Última atualização: **2026-08-19**.
>
> Esta primeira versão foi montada por inspeção do código, do `CHANGELOG.md`
> da raiz e do `git log` de `develop`. Onde ela divergir do código, **o código
> vence** — e quem notar corrige a seção aqui.

## Stack (autoritativa — doc que divergir perde)

FastAPI · SQLAlchemy (ORM, sem Alembic) · **PostgreSQL no Supabase** ·
Celery + Redis · Pydantic v2 (`pydantic-settings`) · Polars (parser de CSV) ·
Supabase Storage (S3) · Docker Compose local.

- **Supabase é SOMENTE auth** (`supabase.auth.get_user(token)`) e Storage.
  Todo dado passa por SQLAlchemy. Não existe PostgREST no caminho.
- **Não há Alembic.** Migrations são SQL solto em `migrations/` (75 arquivos),
  aplicadas por `scripts/apply_migrations.py` ou pela Management API do
  Supabase. Não existe tabela de controle de versão de schema.
- Python **3.12** (`.venv312`). O `.venv` da raiz é 3.9 e **quebra na coleção
  do pytest** — ver `DECISOES.md`.

## Serviços do `docker-compose.yml`

| Serviço | O que é | Porta |
|---|---|---|
| `app` | API FastAPI (uvicorn `--reload`) | **8000** |
| `worker` | Celery worker | — |
| `db` | PostgreSQL local | 5432 |
| `redis` | Broker + backend do Celery | 6379 |
| `minio` | S3 local | 9000 |
| `evolution` | API de WhatsApp (Evolution) | — |

⚠️ O `CLAUDE.md` da raiz manda subir uvicorn na **8081**; o compose e o proxy
do Vite usam a **8000**. O caminho oficial é o compose — **backend roda em
Docker, não no host** (`DECISOES.md`).

## Forma do código

`api/v1/routes/` (fino) → `services/` (regra) → `repositories/` (query) →
`models/` (ORM). Não pule camadas.

- **24 módulos de rota**, registrados em `app/api/v1/routes/__init__.py`.
  `jobs` só entra se `settings.USE_JOBS_PIPELINE` (ligado no compose).
- **~45 services**, **22 repositories**, **29 models**.
- Fora do `/api/v1`: `/cakto/webhook` (compatibilidade) e
  `/webhooks/instagram` (URL cadastrada no painel da Meta, não versiona).
- `/api/v1/internal/cron/*` — alvo do **pg_cron + pg_net do Supabase**,
  protegido por `CRON_SECRET`. Não há Celery Beat.

## Fila do Celery — o detalhe que já quebrou duas vezes

`_fila_do_banco()` (`app/tasks/celery_app.py`) deriva o nome da fila do
**DATABASE_URL**, não de `ENVIRONMENT` (os dois ambientes reportam
"development"). Produção e homologação dividem o mesmo Redis/0; sem isso as
tasks caíam meio a meio no worker errado, que não achava o registro no banco
dele e **retornava em silêncio** — era a causa dos uploads presos em
`pending` para sempre.

`task_default_priority=0`. **Só as pontas 0 e 9 são consumidas** — o default
antigo (5) caía num step intermediário do Redis que ninguém consome e a task
ficava enfileirada para sempre, aceita com 202 e nunca executada. Regra:
priority **0** (interativo) ou **9** (batch). Nunca outro valor.

## Integrações vivas

| Integração | Onde | Estado |
|---|---|---|
| **Shopee Afiliados** | `shopee_graphql_client.py`, `shopee_integration_service.py` | Sync full/incremental, upsert aditivo, painel `/admin/sincronizacoes` |
| **Facebook / Meta Ads** | `facebook_marketing_client.py`, `facebook_integration_service.py` | Campanhas + espelho de gasto/cliques → `AdSpend` |
| **Instagram** | `instagram_*` (5 services), webhook próprio | Automação comentário → direct; exclusiva do plano **MAX** |
| **Kiwify** | `kiwify_service.py`, `charges.py` | Fonte de assinatura em produção |
| **Cakto** | `cakto_service.py` | Provider legado, rota mantida |
| **Evolution (WhatsApp)** | `whatsapp_*` (4 services) | Resumo diário; no ar em **hml**, oculto em produção |
| **OpenAI** | `openai_client.py`, `ai_*` (5 services) | Diagnóstico IA por créditos; oculto em produção |

## Planos

`app/core/plans.py` é a **fonte única** (espelhada em
`marketdash-frontend/src/shared/lib/plans.ts`). `essencial` / `pro` / `max`;
limite `-1` significa **ilimitado** (MAX). Adicionar plano = uma entrada ali.

## Rodar e testar

```bash
docker-compose up                                   # tudo (app na 8000)
PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
```

O `pytest tests/ -v` do `CLAUDE.md` **não funciona** com o venv default.

## Em voo / pendente de humano

- **Rodada 7 do painel admin** validada só contra **homologação**. Itens 1, 2,
  3 e o achado card×lista precisam de reconfirmação contra **produção** — ver
  a seção de pendências no `CHANGELOG.md` da raiz.
- Branch de trabalho: **`develop`**. Produção sai de `main`.
