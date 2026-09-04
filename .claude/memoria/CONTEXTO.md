# Contexto — MarketDash Backend

> **Estado atual do repositório.** Sobrescreva as seções ao mudarem — o
> histórico vive em `DIARIO.md`. Última atualização: **2026-09-04**.
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
| `waha` | API de WhatsApp (WAHA, engine GOWS — substituiu a Evolution em 25/08) | perfil `whatsapp`, porta 3001 |

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
| **Shopee Afiliados** | `shopee_graphql_client.py`, `shopee_integration_service.py` | Sync full/incremental, upsert aditivo, painel `/admin/sincronizacoes`. **Os 24 jobs `shopee-sync-*` do pg_cron ficaram `active=false` de 05/08 a 04/09/2026** — religados em produção; em hml continuam desligados, com só o Luiz (user 9) agendado pela `078`. Sinal de que o cron vive é `sync_runs.trigger`, **não** o `last_sync_at` da tela (que pode ser um clique manual) |
| **Facebook / Meta Ads** | `facebook_marketing_client.py`, `facebook_integration_service.py` | Campanhas + espelho de gasto/cliques → `AdSpend` |
| **Instagram** | `instagram_*` (5 services), webhook próprio | Automação comentário → direct; exclusiva do **MAX**. Direct sai como **template com botão** (Rodada 2), com fallback de texto puro. API em **v25.0** |
| **Kiwify** | `kiwify_service.py`, `charges.py` | Fonte de assinatura em produção |
| **Cakto** | `cakto_service.py` | Provider legado, rota mantida |
| **WAHA (WhatsApp)** | `waha_client` + `whatsapp_*` services | Números/grupos das alunas (F1 do módulo de grupos); hml. **O Resumo diário saiu por inteiro em 03/09/2026** (rotas, services, models, job pg_cron — migration 077), junto com a Blacklist; a sessão global do resumo deixou de existir. `POST /whatsapp/webhook` e o tratamento de status/participantes **ficam** — são infra do módulo de grupos, não do resumo |
| **Proxy por sessão** | `proxy_pool_service`, `proxy_tasks`, `admin_proxies` | Pool de IPs sticky com afinidade por usuária. **Flag LIGADA** (`whatsapp_proxy: true`, 27/08) mas o **pool está vazio** — na prática toda sessão ainda sai pelo IP do servidor, agora com WARNING. Migrations 068 **e 069** em hml (cron horário ativo); **no ar em hml** (API+worker+admin, verificado ponta a ponta em 31/08); produção intocada. Pendências: comprar/cadastrar os proxies e o spike "`stop`→`PUT`→`start` pede QR?" |

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

- **Proxy por sessão (27/08)**: implementado, **flag ligada** e **pool vazio** —
  ou seja, ainda sem efeito prático: cada sessão continua saindo pelo IP do
  servidor, agora com WARNING no log. O que falta, em ordem:
  1. **comprar 2 proxies BR** (1 móvel, 1 residencial — datacenter é queimado)
     e cadastrá-los no admin (Uso e Sistema › Sistema › "IPs das conexões");
  2. rodar o **spike**: aplicar proxy novo numa sessão já pareada em hml e
     responder se o WhatsApp pede novo QR (registrar em `docs/whatsapp-waha.md`);
  3. aplicar a **migration 069** (cron da sonda) — só depois de haver pool, ou
     ela cria um `sync_run` vazio por hora;
  4. `WHATSAPP_PROXY_OBRIGATORIO=true` **só em produção e só com pool com
     capacidade** — com pool cheio/vazio e obrigatório ligado, nenhum número
     novo é criado.
  ⚠️ Números **já pareados não migram sozinhos**: a mudança de IP de um número
  ativo é justamente o sinal a evitar, então é o botão *Realocar*, em lote
  pequeno (um por dia por usuária).
  ⚠️ Em hml/produção **implantados** a flag não muda nada ainda: o backend
  desta feature não foi commitado nem deployado.

- **Painel admin: Rodada 9 FECHADA em 02/09, validada contra PRODUÇÃO**
  (diagnóstico assinante a assinante + backfill de `is_plan_change` rodado em
  prod; hml tinha 0 eventos). Regras vigentes: frequência normalizada só em
  `_norm_freq` (plans.py); bruto do MRR = `product_base_price` do webhook;
  produtor conta churn; pareamento de upgrade por plano normalizado. A nota
  antiga da Rodada 7 (validada só contra hml) está superada por esta.
- **Automação Instagram: EM PRODUÇÃO desde 02/09** (App Review aprovado
  01/09, as 3 permissões com Advanced Access). Migrations 052–056 aplicadas
  em produção, cron de token agendado (202 validado), envs `INSTAGRAM_*` na
  API e no worker, gate de ambiente removido no frontend (o de plano MAX
  fica). Swap do webhook e E2E real FEITOS na manhã de 02/09 (reply+DM
  `enviado`; hml testa via `scripts/simular_comentario_instagram.py`).
  **Automação em STORY** (reply→DM via webhook `messages`, migration 072,
  escopos story_especifico/story_qualquer) EM PRODUÇÃO desde 02/09 ~10h50
  (autorizada pelo João; 072 aplicada em prod, 3 contas re-inscritas com
  comments+messages). Falta só o João assinar o campo `messages` no painel
  Meta para as DMs serem entregues. `GET /me/stories` confirmado na nossa variante (200 com o
  story real). Ver CHANGELOG 2026-09-02.
- **Migrations**: 058 (grupos WhatsApp) APLICADA em hml em 25/08; **070**
  (`whatsapp_instancias.envio_pausado`/`pausado_em`) aplicada e conferida em
  hml em 31/08 — ⚠️ é `ALTER TABLE`, a armadilha *inversa* do `create_all`:
  subir o model antes dela quebra `GET /instancias` com `UndefinedColumn`; **052–056 (Instagram) APLICADAS em produção em 02/09** (a feature
  ligou); 045–046 (WhatsApp) seguem fora de produção enquanto a feature
  estiver desligada lá. ⚠️ A nota anterior
  dizia que 049–055 não estavam em produção, mas isso é **falso para 049/050**:
  a tela de Campanhas carrega em produção usando `ad_review_issue` e
  `status_active_since` via ORM, o que seria `UndefinedColumn` se as colunas não
  existissem. **Nunca confiar nesta tabela — medir no banco** com
  `information_schema` antes de qualquer deploy.
- Branch de trabalho: **`develop`**. Produção sai de `main`.
