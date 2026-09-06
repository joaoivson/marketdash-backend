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
| **Kiwify** | `kiwify_service.py`, `charges.py`, `webhook_helpers.py` | Fonte de assinatura em produção. **A conta só é renomeada pelo CPF em evento que LIBERA acesso** (`allow_email_update=(action == "activate")`): estorno e cancelamento chegam com o e-mail do **pedido antigo** e podem chegar depois de uma recompra — renomear ali devolvia a conta paga para o e-mail errado e o login criava conta nova, sem assinatura (caso Anne, 03-04/09/2026) |
| **Cakto** | `cakto_service.py` | Provider legado, rota mantida |
| **WAHA (WhatsApp)** | `waha_client` + `whatsapp_*` services | Números/grupos das alunas (F1 do módulo de grupos); hml. **O Resumo diário saiu por inteiro em 03/09/2026** (rotas, services, models, job pg_cron — migration 077), junto com a Blacklist; a sessão global do resumo deixou de existir. `POST /whatsapp/webhook` e o tratamento de status/participantes **ficam** — são infra do módulo de grupos, não do resumo. ⚠️ Desde a 079 (04/09) o evento de participantes grava o **número real** além do hash, e desde a **080** (04/09b) a lista de membros de grupo ATIVADO é persistida em `grupo_participantes` — ver "Em voo" |
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

- **Roteiros: rodada de 06/09 EM HOMOLOGAÇÃO.** Migration **082** aplicada em
  hml (`passo_blocos`, `offset_segundos`/`offset_unidade`,
  `acao_descontinuada`, `blocos_enviados`, `tipo_conteudo` texto/midia →
  `mensagem`, índice único `uq_roteiro_execucao_ativa`). Produção **PENDENTE**.

  ⚠️ **O job `roteiros-tick-5min` NÃO EXISTE em produção** (medido em 06/09).
  Sem ele nenhum roteiro dispara lá, e o sintoma é exatamente "agendei e não
  saiu". Vem da migration **061**, que está na lista de pg_cron do runbook
  (passo 10), não na §8.1 — conferir antes de liberar o módulo.

  ⚠️ **A 082 cria TABELA** (`passo_blocos`), e blocos carregam o texto que a
  afiliada escreve. Se a API subir antes da migration, `create_all` cria a
  tabela **sem RLS**. Ela também **converte dado** (`tipo_conteudo`), então
  rodar depois do deploy deixa passo antigo com um tipo que o código novo não
  reconhece. Produção tem 0 linhas nas 6 tabelas de roteiros — a conversão é
  no-op lá, mas a ordem continua importando pelo `create_all`.

  **Dívida conhecida:** `marcar_todos` é aceito, gravado e **nunca usado** — o
  `waha_client` não tem `mentions`. É a mesma família do bug que a rodada
  corrigiu (entrada aceita e ignorada em silêncio), e ficou fora do escopo do
  documento.

- **Campanhas de grupos: rodada de correções (04/09) EM HOMOLOGAÇÃO.**
  Migration **079** aplicada em hml (`campanha_numeros` + backfill,
  `campanhas.limite_participantes`, `grupo_eventos.identificador` e
  `identificador_tipo`); backend e frontend deployados e validados contra a URL
  real. Produção não tem o módulo, então lá nada foi aplicado.

  **A privacidade INVERTEU, e a política já acompanhou.** Desde a 079,
  `grupo_eventos` guarda o **número real** de quem entra no grupo — o hash
  continua ao lado (é ele que casa entrada com saída), mas deixou de ser a única
  coisa guardada. A política prometia o contrário e foi reescrita em 04/09
  (`marketdash-frontend/src/features/landing/pages/PrivacyPolicy.tsx`): o que
  guardamos, para quê, quem é o controlador (a afiliada — nós tratamos por conta
  e ordem dela) e a retenção. Detalhe em `docs/PROMOCAO_PARA_PRODUCAO.md` §3.8.

  ⚠️ **A ordem importa na promoção**: a política publicada **antes** da 079 ir
  a produção, não depois.

  ⚠️ **Dívida conhecida**: apagar o contato de **uma** pessoa que peça é
  **manual, via suporte** — não existe endpoint nem tela. É o que a política
  promete hoje; se o volume crescer, vira ferramenta.

- **`FRONTEND_URL` corrigida em hml (05/09).** A tela de homologação mostrava o
  link de entrada com o domínio de **produção** (`marketdash.com.br/g/{slug}`),
  onde o módulo não existe — daí o "a página do grupo não funciona". A env
  estava setada como produção nos dois recursos de hml no Coolify (API
  `r448swsggoock0wg80csws0k` e worker `jos0k8so0gw4c8okkgg8kskg`), e o `.env`
  tinha a chave **duplicada** (a última vence no `python-dotenv`). Corrigida nos
  dois; produção conferida e intocada.

  A base agora deriva de `app/core/ambiente.identidade_do_banco`, **não** de
  `ENVIRONMENT` — a API de hml reporta `"environment":"development"`, a mesma
  armadilha da fila do Celery, e chavear por ela mandaria produção para
  `localhost` se a env sumisse.

- **Segunda rodada (04/09b): migration 080 aplicada em hml.**
  `campanha_grupos.cheio_override`, `grupo_participantes` e `campanha_sub_ids`.

  **O telefone da 079 nunca chegou a ser gravado** — não por defeito da cadeia
  nova, que estava correta, mas porque o webhook lia `JID` antes de
  `PhoneNumber`, e em grupo com endereçamento LID o `JID` **é** o `…@lid`.
  Medido: 49 de 49 eventos pós-deploy nasceram `identificador_tipo='lid'`.
  Corrigido lendo os dois como campos separados; a identidade manteve a
  precedência de antes porque é ela que vira `identificador_hash`.

  ⚠️ **O telefone só existe com número CONECTADO (05/09).** O webhook do WAHA
  **não manda `PhoneNumber`** — provado com 191 eventos gravados depois da
  correção que passou a ler o campo, todos ainda `lid`. Quem tem o número é o
  payload REST de `/groups`, então o telefone (na lista de participantes E nos
  eventos antigos, preenchidos pelo hash) depende de o sync rodar. Em hml os
  números estão desconectados: reconectar e sincronizar é o que falta para
  validar de ponta a ponta.

  ⚠️ **A 080 herda o bloqueio jurídico da 079, e mais forte.**
  `grupo_participantes` guarda a **lista de membros** — não só a contagem, como
  era até 04/09. Segunda inversão de LGPD em dois dias, e sem alternativa:
  "exportar quem está no grupo agora" não tem outra fonte, e derivar de
  `grupo_eventos` cobriria 472 dos 946 membros de um grupo real. O recorte é o
  mínimo que atende: **só grupo ativado**. A política já foi reescrita de novo
  (`PrivacyPolicy.tsx`) e precisa estar publicada antes de a 080 ir a produção.

  ⚠️ **`WHATSAPP_HASH_SALT` é opcional, e esse é o perigo.** Sem ela,
  `_segredo_do_hash()` deriva de `SHOPEE_ENCRYPTION_KEY` (e, na falta,
  `JWT_SECRET`; sem nenhum, recusa gravar). Definir a env **depois** do primeiro
  evento troca a origem do segredo e o casamento entrada↔saída para de bater —
  **sem erro nenhum**, porque o hash novo simplesmente não encontra o antigo.
  Fixe antes do primeiro evento em produção.

  Duas consequências que valem para quem for mexer:
  - **LID.** Quem oculta o número chega como `84729130@lid`, id opaco que não
    disca. `classificar()` (`grupo_evento_service.py`) separa telefone de LID, e
    o CSV sai com a coluna `telefone` **vazia** no caso do LID — inventar um
    número ali daria uma lista de contatos que não existe.
  - **A regra de lotação existe uma vez só**: `TETO_SQL`/`teto_efetivo()` em
    `campanha_link_repository.py`. É lida pelas 3 queries de rotação **e** pelo
    contador da Visão geral. Duplicá-la faz a tela dizer "há vaga" num grupo que
    o roteador já não escolhe.

- **Terceira rodada (05/09b): migration 081 aplicada em hml.**
  `campanha_link_eventos.resultado` (+ índice parcial) e
  `whatsapp_grupos.sub_id` alargado para `VARCHAR(64)`.

  **`ALTER TABLE` pura, sem bloqueio jurídico — mas obrigatória antes do
  deploy.** Sem a coluna `resultado`, o INSERT de clique cita coluna
  inexistente e **todo** clique no link de entrada quebra, não só o de
  fallback. O boot-ALTER de `db/base.py` cobre, mas é rede, não garantia.

  ⚠️ **A rotação agora tem DUAS regras, e a diferença é uma linha.**
  `TETO_SQL` (limite da campanha, acima) governa o caminho normal;
  `CAPACIDADE_SQL` (só a `capacidade` do WhatsApp) governa o **fallback de
  lotado**, quando nenhum grupo tem vaga e o link manda para o primeiro da
  ordem em vez de mostrar "vagas esgotadas". O fallback também é o único
  caminho **sem `FOR UPDATE SKIP LOCKED`**: ali não há vaga a distribuir, e o
  lock faria o segundo clique simultâneo cair na tela que o fallback existe
  para evitar. Mexer numa das duas sem olhar a outra é como o teto vira
  inconsistente.

  ⚠️ **`cheio_override` tem TRÊS estados** (`NULL` = automático). O
  comportamento de 04/09b — limpar o override quando a escolha coincidia com o
  automático — foi **revogado**: ele causava o bug relatado como "o sync apaga
  minha marcação". Marcar "Sim" num grupo já cheio pela ocupação gravava
  `null`, nada era persistido, e o sync seguinte baixava a contagem. Nada mais
  limpa o override sozinho; "Automático" é a porta de volta.

  ⚠️ **`sub_id` de grupo: dois formatos, para sempre.** Grupo novo nasce
  `grupo`+nome sanitizado+sufixo (`grupobeatriz2k7f`); os antigos (`wgea`)
  ficam. A geração é **uma vez, na ativação, nunca rederivada** — renomear o
  grupo não muda o Sub ID, e migrar os antigos seria perda de atribuição
  permanente.

  **Medido antes de codar, e três diagnósticos do documento caíram:** a
  divergência export × tela é defasagem (a lista é do último sync, o contador
  anda pelo webhook — a aritmética fecha, com o −1 sendo nosso próprio número);
  a agregação de Resultados está correta (o grupo #1 mostra 1 entrada porque
  está cheio e a rotação manda tudo para o #2); e `campanha_links` sempre
  guardou só o slug — o domínio errado era a `FRONTEND_URL`.

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

- **A performance do dashboard (04/09) ESTÁ em produção**, por cherry-pick em
  `main` — não por merge da develop. O fix do rename de e-mail, não.

- **Fix do rename de e-mail por CPF (04/09) não está em produção.** O dado da
  aluna afetada já foi corrigido à mão no banco de prod; o **código** só existe
  na `develop` (sobe com a leva das outras demandas). Até subir, qualquer
  recompra que corrija um e-mail digitado errado repete o caso: estorno do
  pedido velho renomeia a conta de volta e a assinante paga vê "Assinatura
  Necessária". Sintoma sem erro nenhum no log — o rastro é `users.updated_at`
  no horário do **estorno** e duas linhas em `users` para o mesmo CPF.

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
- **Migrations**: 058 (grupos WhatsApp) APLICADA em hml em 25/08; **074**
  (`whatsapp_grupos.ativado`), **079**, **080** e **081** (campanhas de grupos,
  ver acima) aplicadas em hml — nenhuma delas em produção; **070**
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
