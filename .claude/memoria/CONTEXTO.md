# Contexto — MarketDash Backend

> **Estado atual do repositório.** Sobrescreva as seções ao mudarem — o
> histórico vive em `DIARIO.md`. Última atualização: **2026-08-21**.
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

## Removido

**Diagnóstico IA (22/08/2026).** Era a única feature de IA do produto e saiu por
inteiro: rota, 6 services (incluindo `openai_client.py`, único ponto de rede com
LLM), 2 repositories, 2 models, 1 schema, 7 testes — e as chaves `OPENAI_*` do
config. Migration `057` dropou as 3 tabelas em hml (em produção foi no-op: as
043/044 nunca chegaram lá).

Cuidado com **falso positivo de busca por "IA"**: resumo do WhatsApp (f-string +
`KpiService`), automação de Instagram (templates da aluna), `insight` de cliques
(agregação SQL), `insights` de campanha (endpoint de métricas da Meta) e
`campaign_service._health` (heurística de 5 linhas) **não são IA** e continuam
vivos. "Orquestra IA" é a razão social da empresa.

## Integrações vivas

| Integração | Onde | Estado |
|---|---|---|
| **Shopee Afiliados** | `shopee_graphql_client.py`, `shopee_integration_service.py` | Sync full/incremental, upsert aditivo, painel `/admin/sincronizacoes` |
| **Facebook / Meta Ads** | `facebook_marketing_client.py`, `facebook_integration_service.py` | Campanhas + espelho de gasto/cliques → `AdSpend` |
| **Instagram** | `instagram_*` (5 services), webhook próprio | Automação comentário → direct; exclusiva do **MAX**. Direct sai como **template com botão** (Rodada 2), com fallback de texto puro. API em **v25.0** |
| **Kiwify** | `kiwify_service.py`, `charges.py` | Fonte de assinatura em produção |
| **Cakto** | `cakto_service.py` | Provider legado, rota mantida |
| **Evolution (WhatsApp)** | `whatsapp_*` (4 services) | Resumo diário; no ar em **hml**, oculto em produção |

## Planos

`app/core/plans.py` é a **fonte única** (espelhada em
`marketdash-frontend/src/shared/lib/plans.ts`). `essencial` / `pro` / `max`;
limite `-1` significa **ilimitado** (MAX). Adicionar plano = uma entrada ali.

## Interruptores de emergência (env var, sem redeploy)

| Variável | Efeito | Quando usar |
|---|---|---|
| `INSTAGRAM_DM_FORMATO=texto` | Direct volta ao formato antigo (link colado no fim da mensagem, sem botão) | Se a Meta recusar o template `button` em produção |

Lida em `app/core/feature_flags.py`, **antes** do `feature-flags.json` — o
arquivo é versionado e exigiria rebuild de imagem. Valor inválido cai no
default, não desliga calado.

⚠️ Em operação normal a variável **não deve existir**. Deixá-la em `texto`
desliga o botão em silêncio e o QA valida o formato errado.

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
- **Automação Instagram**: Rodadas 1 e 2 no ar em homologação. Falta o
  **App Review** — em Standard Access só admin/dev/tester do app completam o
  OAuth, então aluna comum trava na autorização. O screencast é gravado depois
  da Rodada 2, para o vídeo bater com a tela final.
- **Migration 056** aplicada em homologação; **não** em produção (nem 049–055).
- Branch de trabalho: **`develop`**. Produção sai de `main`.
