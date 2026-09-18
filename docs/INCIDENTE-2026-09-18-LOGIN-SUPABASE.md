# Incidente 18/09/2026 — login fora do ar (segunda queda em 24h)

| | |
|---|---|
| **Sintoma** | Login não completa; Advisor do Supabase: *Auth error rate persistently high* (88,89% de falha). Toda a API autenticada falha junto. |
| **Janelas** | 17/09 14h–15h BRT (144 erros de Auth) e 18/09 7h–8h BRT (459 erros de Auth). |
| **Mitigação aplicada** | Reset do projeto no Supabase (2x) e upgrade de compute **nano → Micro** em 18/09 (grátis no plano Pro). |
| **Status** | Causa raiz identificada nos logs. Correção nesta branch (`hotfix/login-supabase-io-esgotado`). |

## O que os logs mostraram

Sequência idêntica nas duas quedas, hora de Brasília:

1. **Campanha dispara, cliques chegam.** Logs do Postgres: `UPDATE custom_links SET
   click_count=62480 …` em **117 s**, `INSERT INTO custom_link_events` em 10–32 s,
   `SELECT custom_links …` em 91–108 s, `still waiting for ShareLock`. Padrão SQLAlchemy —
   é a API no VPS, não o Supabase.
2. **Conexões abortadas em massa.** `could not accept SSL connection: EOF detected` — 852
   em 24h, 300 numa única hora. `canceling statement due to statement timeout` — 225.
3. **Disco no mínimo.** `checkpoint complete: wrote 529 buffers … total=217 s` (escrever
   4 MB levou 3,5 min). Painel: *"about to deplete its Disk IO Budget — throughput will
   return to its baseline of 5 MB/s"*. Compute nano: 0,5 GB RAM, IO por crédito.
4. **GoTrue não consegue conexão.** 394 × `context deadline exceeded` em `/token`; 70 ×
   `error finding refresh token: failed to connect to host=localhost … dial tcp [::1]:5432:
   operation was canceled`. **Não há erro de credencial em lugar nenhum**: o Auth só não
   consegue abrir conexão com o próprio banco.
5. **Todo mundo cai, não só quem está logando.** 5.895 dos 6.622 requests de Auth em 24h
   eram `GET /auth/v1/user` — a API validando o token de *cada* requisição com
   `supabase.auth.get_user()`. Essa chamada lê o banco. Banco travado = usuária logada
   também recebe 401/504.
6. **Restart em loop.** `ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS …` e
   `ALTER TABLE facebook_integrations …` rodaram **20 vezes cada** em 24h (14 numa hora):
   é `init_db()` no startup. O `HEALTHCHECK` do container batia em `/health`, que consulta
   o banco → banco lento → *unhealthy* → Coolify reinicia → startup roda `create_all` +
   `ALTER TABLE` (lock exclusivo) no banco já sufocado → mais lento.
7. **Por que o reset "resolve".** Reiniciar o projeto devolve o crédito de IO e derruba as
   conexões presas. Dura até a próxima campanha.

O fix de 17/09 (`440fad1`, incremento atômico + `lock_timeout`) estava em `main` e
`develop`, mas **os logs de 18/09 ainda mostram `click_count=4712` com valor literal** —
produção rodava o código anterior.

## Causa raiz

Três problemas de código que se reforçam, sobre uma instância subdimensionada:

| # | Problema | Efeito |
|---|---|---|
| 1 | Cada clique = SELECT link + SELECT assinatura + INSERT evento + UPDATE contador, síncronos, na mesma linha quente | Esgota IO e conexões numa campanha grande |
| 2 | Token validado por chamada de rede ao GoTrue em toda requisição | Banco lento derruba a API inteira, não só o login |
| 3 | DDL (`create_all` + `ALTER TABLE`) no startup + healthcheck que depende do banco | Loop de restart que alimenta o problema 1 |

## Correção (esta branch)

| Área | Mudança | Arquivo |
|---|---|---|
| Cliques | Redirect não escreve no Postgres: `HINCRBY` + `RPUSH` no Redis; descarga em lote pelo worker (1 UPDATE por link + 1 INSERT em lote), autoagendada a cada `CLIQUES_FLUSH_INTERVALO_S` (15 s). Sem Redis, cai no incremento atômico de 17/09. | `app/services/click_buffer.py`, `app/tasks/click_tasks.py`, `app/services/custom_link_service.py` |
| Cliques | Decisão do redirect (url/erro) em cache por 60 s: o caminho quente não consulta o banco. Invalidado em update/delete do link. | `custom_link_service.py::_resolver_slug` |
| Auth | JWT verificado localmente (JWKS ES256 com cache em memória, ou `SUPABASE_JWT_SECRET` HS256). `auth.get_user` só como retaguarda quando não há chave configurada, ou com `AUTH_VALIDACAO_LOCAL=false`. | `app/core/supabase_jwt.py`, `app/api/v1/dependencies.py` |
| Startup | `init_db()` só faz `SELECT 1`. `create_all` atrás de `DB_SCHEMA_NO_STARTUP` (dev/test). As duas colunas viraram `migrations/087_*.sql`. | `app/db/base.py` |
| Container | `HEALTHCHECK` aponta para `/health/live` (sem banco). `/health` continua para readiness e para o monitor externo. | `app/main.py`, `Dockerfile` |

### Efeitos colaterais aceitos
- Contador do link no painel atrasa até 15 s (descarga em lote).
- Desativar/expirar um link demora até 60 s para valer no redirect (cache).
- Se o worker estiver fora, os cliques acumulam no Redis e são gravados quando ele voltar.
  Nada é perdido; `click_buffer.tamanho_pendente()` expõe o tamanho da fila.

## Variáveis de ambiente (Coolify — API e worker)

| Variável | Valor | Obrigatória? |
|---|---|---|
| `SUPABASE_JWT_SECRET` | Supabase → Settings → API → *JWT Secret* (só se o projeto ainda assina em HS256) | Não — sem ela e sem JWKS a API usa `auth.get_user` como antes |
| `SUPABASE_JWKS_URL` | vazio (deriva de `SUPABASE_URL`) | Não |
| `AUTH_VALIDACAO_LOCAL` | `true` (default) | Não — `false` desliga o caminho novo |
| `CLIQUES_BUFFER_REDIS` | `true` (default) | Não — `false` volta ao incremento atômico |
| `DB_SCHEMA_NO_STARTUP` | ausente em prod/hml | Não — só `true` em dev |
| `DATABASE_URL` | **Conferir** que aponta para o pooler (`…pooler.supabase.com:6543`, modo transaction), não para `db.<ref>.supabase.co:5432` | — |

Verificar em Supabase → Settings → API → *JWT Keys* se o projeto já está em
**ES256** (chaves assimétricas). Se sim, nada a configurar — o JWKS é público.

## Plano de deploy

1. **HML primeiro.** Merge desta branch em `develop` → deploy automático em HML.
2. **Teste de carga em HML:** `k6 run tests/load/k6_redirect_cliques.js` (500 req/min no
   mesmo slug por 5 min). Critérios: p95 do redirect < 300 ms; zero 5xx; no Supabase de HML,
   **nenhum** `UPDATE custom_links` por clique nos logs (só 1 a cada ~15 s por link); login
   funcionando durante o teste.
3. Validar no log da API: `Autenticação Supabase OK` sem `usando auth.get_user` (se aparecer,
   falta `SUPABASE_JWT_SECRET` ou o JWKS não respondeu).
4. **Produção em horário de baixo uso** (após 22h). Merge em `main` dispara
   `deploy-production.yml`. Confirmar `version` em `https://api.marketdash.com.br/health`
   igual ao SHA do merge.
5. Acompanhar 30 min: Supabase → Reports → Database (IO, conexões) e Auth logs (`GET /user`
   deve cair para perto de zero).
6. Rollback: `deploy-production.yml` com o SHA anterior (`440fad1`), ou por env
   `AUTH_VALIDACAO_LOCAL=false` / `CLIQUES_BUFFER_REDIS=false` sem redeploy de código.

## Dependência de Redis — conferido em 18/09

A pergunta veio da migração do build para o GitHub Actions: o pipeline teria
desabilitado o Redis? **Não.** O que foi medido hoje:

- `GET https://api.marketdash.com.br/health` → `"redis":"connected"`
- `GET https://api.hml.marketdash.com.br/health` → `"redis":"connected"`
- O pipeline não gerencia Redis. Os workflows só constroem a imagem, empurram
  para o GHCR e fazem PATCH da tag na app do Coolify. Redis é um **recurso
  separado**, nunca declarado nos workflows nem no `Dockerfile`; o
  `docker-compose.yml` com serviço `redis` é só ambiente local.
- O que provavelmente gerou a impressão está documentado em
  `.claude/memoria/STATUS-painel-infra-admin.md`: o Coolify oscila o status dos
  Redis entre `running:healthy` e `exited:unhealthy` **com o serviço
  funcionando o tempo todo** — foi por isso que o painel de infra passou a
  cruzar o status do Coolify com o `/health` real.
- Segue de pé o achado da etapa 16b: existem **duas** instâncias de Redis, e só
  a `h0cw0gc8owws004480g0sog8` é usada (api prod, worker prod e api hml apontam
  para ela). A `y4so0kk48sg8woskskok8owo` não tem referência conhecida. Apagar
  a nº 2 continua pendente — e é ação destrutiva em infra compartilhada, a ser
  feita sozinha, nunca junto deste hotfix.

### O que o `/health` NÃO prova

Ele testa o Redis a partir da **API**. O worker Celery é outra app, com suas
próprias envs, e é ele quem descarrega o buffer de cliques. Por isso o `/health`
passou a devolver `cliques_pendentes` (`LLEN` do buffer): número que cresce e
não drena = worker sem consumir. Antes do deploy em produção, confirmar que o
worker tem o mesmo `REDIS_URL` da API.

### Se o Redis cair, o que acontece

| Cenário | Efeito | Perda de dado |
|---|---|---|
| Redis indisponível para a API | `registrar()` devolve False → incremento atômico direto no banco (fix de 17/09). Dedup de 60 s também para, então clique duplicado pode contar duas vezes | Nenhuma |
| Redis de pé, worker parado | Cliques acumulam no buffer; contador do painel congela até o worker voltar | Nenhuma |
| Redis reiniciado sem persistência | Perde o que estava no buffer: no máximo ~15 s de contagem de cliques | Só contador de clique |
| Banco lento na descarga | Lote volta ao Redis e a task tenta de novo com backoff | Nenhuma |

Em nenhum cenário o redirecionamento falha, e nada disso toca dado de usuária,
dataset, assinatura ou campanha — o buffer só carrega `(link_id, user_id,
timestamp)` de clique.

## Depois do hotfix
- Alertas no Supabase (IO budget, RAM, conexões) e sonda sintética de login a cada 5 min.
- Medir uma semana em Micro; decidir Small com dados (ver `docs/PLANO_ESCALA_100_USUARIAS.md`).
- `trigger_facebook_sync` está em pg_cron com `job startup timeout` — mover para Celery.
- Front reenvia token expirado (301 × `GET /user` 403 em 24h): revisar refresh no cliente.
