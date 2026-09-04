# Promoção para produção — Grupos de WhatsApp e Instagram

> **Nada deste documento foi executado.** Por decisão do João (25/08/2026,
> reafirmada em 31/08/2026), tudo vive em `develop`/homologação até ser
> homologado. Estado do banco **medido em 26/08/2026** (linhas `058`–`067`) e em
> **31/08/2026** (`068`–`070`), não lembrado — a seção 1 traz o script para
> remedir, porque nota sobre estado de banco apodrece.
>
> **A seção 8 é o inventário fechado do que falta promover** — migrations,
> arquivos e variáveis. Comece por ela; as seções 3 e 4 são o detalhe de cada
> item.

Documento único das duas features. O runbook antigo
(`PROMOCAO_MODULO_GRUPOS.md`) continua valendo para o detalhe do módulo de
grupos, mas **estava desatualizado**: foi escrito antes da migration `067`.

---

## 0. A regra que governa tudo: `create_all` chega antes da migration

`Base.metadata.create_all()` roda no **boot da API** (`app/db/base.py:init_db`).
Todo model importado que não tem tabela **ganha uma tabela nova, sem RLS**.

Isso não é teórico — **já aconteceu com o Instagram em produção**. A medição de
26/08 mostra lá:

| Evidência | Leitura |
|---|---|
| `instagram_connections/automations/events` existem | as tabelas estão lá |
| colunas de `054`, `055` e `056` presentes | mas elas vêm dos **models**, não das migrations |
| **zero policies** (hml tem 1 em cada) | a `052`, que cria as policies, **nunca rodou lá** |
| cron `instagram-token-refresh` ausente | a `053` também não |

Ou seja: as tabelas de produção foram criadas pelo **boot**, não pela migration.
Quem olhasse só "a coluna existe?" concluiria que as migrations foram aplicadas.
Não foram.

**Consequência prática:** RLS ligado sem policy nenhuma nega tudo para
`anon`/`authenticated` — então **não é vazamento**; o PostgREST do Supabase não
serve essas tabelas. E a API funciona porque conecta como `postgres`, que tem
`BYPASSRLS` (medido). Mas a segunda linha de defesa que a `052` promete **não
existe em produção**, e a divergência com hml é real.

> **Por que os Grupos ainda não sofreram disso:** a `main` não tem o código do
> módulo, então o boot não tem model para criar. **Isso muda no instante do
> merge.** Se o deploy subir antes das migrations, as ~20 tabelas do módulo
> nascem sem RLS em produção — e aí sim há dado de aluna sem isolamento.

**A ordem não é preferência, é a única ordem correta: migration → deploy.**

---

> **Nota de 04/09/2026 — o `ensure_rls` de homologação não é rede em produção.**
> Ao aplicar a `079` descobriu-se que **hml** tem um event trigger `ensure_rls`
> que liga RLS em toda tabela nova: foi ele que protegeu `campanha_numeros`,
> criada pelo `create_all` antes de a migration chegar. **Não foi confirmado que
> produção tem o mesmo trigger** — até que alguém meça (a query da seção 0
> resolve), a regra deste documento continua valendo por inteiro.

## 1. Estado medido em 26/08/2026 (produção) e 31/08/2026 (homologação)

> **Homologação remedida em 31/08/2026 e está completa**: as 22 tabelas do
> módulo existem, as 8 colunas de `ALTER TABLE` estão presentes (incluindo
> `envio_pausado`/`pausado_em` da `070`) e os três crons — `roteiros-tick-5min`,
> `grupos-snapshot-3am-brt` e `proxy-health-horario` — estão agendados.
> **Produção segue intocada**, por decisão.

### Módulo de Grupos

| Migration | Tabelas | Produção | Homologação |
|---|---|---|---|
| `058` | `whatsapp_instancias`, `whatsapp_grupos`, `whatsapp_grupo_instancias` | ausente | OK (RLS) |
| `059` | `campanhas`, `campanha_grupos` | ausente | OK (RLS) |
| `060` | `templates_mensagem`, `template_variacoes`, `roteiros`, `roteiro_passos`, `roteiro_execucoes`, `roteiro_mensagens`, `blacklist_numeros` | ausente | OK (RLS) |
| `061` | *(pg_cron roteiros)* | ausente | **agendado** |
| `062` | `integracoes` | ausente | OK (RLS) |
| `063` | `campanha_links`, `campanha_link_eventos`, `grupo_eventos`, `grupo_snapshots` | ausente | OK (RLS) |
| `064` | *(pg_cron snapshot)* | ausente | **agendado** |
| `065` | `campanha_anuncios` + `campaign_daily_insights.leads` | ausente | OK |
| `066` | `monitoramentos`, `monitoramento_capturas` | ausente | OK (RLS) |
| `067` | `conexao_convites` + `blacklist_numeros.numero_mascarado` | ausente | OK (RLS) |
| `068` | `whatsapp_proxies` + `whatsapp_instancias.proxy_*` | ausente | OK |
| `069` | *(pg_cron saúde de proxy)* | ausente | **agendado** |
| `070` | `whatsapp_instancias.envio_pausado` + `.pausado_em` | **ausente** | OK (31/08) |

Produção está **limpa**: nenhuma tabela do módulo existe lá. É o cenário bom.

⚠️ **A `070` é a armadilha inversa da regra da seção 0.** Ela não cria tabela,
faz `ALTER TABLE` em `whatsapp_instancias` — e `create_all` **não adiciona
coluna em tabela que já existe**. Se o deploy do model subir antes dela,
`GET /instancias` quebra com `UndefinedColumn` para toda usuária MAX. Aplicar
**antes** do deploy, sem exceção.

### Instagram

| Migration | Produção | Homologação |
|---|---|---|
| `052` (tabelas + **policies**) | tabelas sim, **policies NÃO** | completo |
| `053` (cron de renovação de token) | **ausente** | agendado |
| `054` / `055` / `056` (colunas) | colunas presentes (via `create_all`) | presentes |

### Como remedir (leitura pura, nada escreve)

```sql
-- rode nos DOIS ambientes e compare
SELECT c.relname,
       c.relrowsecurity AS rls,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND c.relname IN (
     'whatsapp_instancias','whatsapp_grupos','whatsapp_grupo_instancias',
     'campanhas','campanha_grupos','templates_mensagem','template_variacoes',
     'roteiros','roteiro_passos','roteiro_execucoes','roteiro_mensagens',
     'blacklist_numeros','integracoes','campanha_links','campanha_link_eventos',
     'grupo_eventos','grupo_snapshots','campanha_anuncios','monitoramentos',
     'monitoramento_capturas','conexao_convites',
     'instagram_connections','instagram_automations','instagram_events')
 ORDER BY 1;

-- colunas que vêm de ALTER TABLE (não confie nelas para dizer "migration aplicada")
SELECT table_name, column_name FROM information_schema.columns
 WHERE (table_name='campaign_daily_insights' AND column_name='leads')
    OR (table_name='blacklist_numeros'       AND column_name='numero_mascarado')
    OR (table_name='user_settings'           AND column_name='whatsapp_envio_config')
    OR (table_name='whatsapp_instancias'     AND column_name='envio_pausado');

-- crons agendados
SELECT jobname, schedule FROM cron.job ORDER BY jobid;
```

> ⚠️ **`policies=0` não é defeito — não "conserte".** Onze tabelas do módulo
> aparecem com RLS ligado e zero policies: `whatsapp_grupo_instancias`,
> `campanha_grupos`, `template_variacoes`, `roteiro_passos`, `campanha_links`,
> `campanha_link_eventos`, `grupo_eventos`, `grupo_snapshots`,
> `campanha_anuncios`, `monitoramento_capturas` e `whatsapp_proxies`.
> **Nenhuma delas tem `user_id`** — o dono chega por join com a tabela pai — e
> as migrations ligam RLS sem policy de propósito (o comentário está na `063` e
> na `066`). RLS sem policy **nega tudo**, que é o estado mais restritivo
> possível. Escrever uma policy ali exigiria inventar um `user_id` que não
> existe. Conferido em hml em 31/08/2026.
>
> A leitura que **é** defeito é a inversa: tabela **com** `user_id`, RLS ligado
> e zero policies — foi o caso do Instagram em produção (seção 0).

**Toda migration daqui é idempotente e aditiva** — `IF NOT EXISTS` em tudo e
`DROP POLICY IF EXISTS` antes de cada `CREATE POLICY` (conferido nas dez).
Reaplicar é seguro. Nenhuma tem `DROP TABLE`/`DROP COLUMN`/`TRUNCATE`.

---

## 2. O que mais vai no merge — leia antes de aceitar

"Subir o módulo de grupos" **não autoriza** subir o backlog de `develop` junto.
Em **31/08/2026** são **49 commits no backend** e **33 no frontend** (eram 31 e
16 em 26/08 — a distância cresce a cada rodada; **conte de novo no dia**). O que
**não** é do módulo de grupos:

| Commit | O que é | Decisão |
|---|---|---|
| `1e300aa` | fix: CSV não some quando o broker está fora do ar | correção real, independente do módulo |
| `e8aba4d` / `caea8e9` | fix: token Meta expirado deslogava a usuária (401 → 409) | correção real, independente |
| `c36b6d5` / `d924d6c` | chaves novas do Supabase (`sb_publishable_`/`sb_secret_`) | aditivo; as antigas seguem funcionando |
| `f294cf7` | `CHANGELOG.md` versionado | doc |
| `1e300aa` | fix: CSV sumia com o broker fora do ar | correção real, independente |
| `457a478` | fix: insights do Meta vinham vazios e o gasto sumia da tela | **correção de dado da aluna** — independente do módulo |
| `1bd8d6a` | fix: runs presos em `running` para sempre | correção real, independente |
| `0a0c657` | fix(shopee): canal vem do referrer, não do `channelType` | muda número na tela — decidir com o João |
| `cc7e91c` / `e6f17b6` | feat(ofertas): vitrine e ordenação por vendas | **feature nova**, não é do módulo |
| `6e50f2a` | feat(shopee): passo a passo da API de Afiliada | feature nova (frontend) |
| `2cbf031` … `e0a08eb` | rodada mobile (7 commits no frontend) | melhoria transversal — atinge telas que JÁ estão em produção |

Os demais são F0→F8 do módulo. Confira sempre com:

```bash
git log --oneline main..develop            # nos DOIS repos
```

---

## 3. Grupos de WhatsApp

### 3.1 Ordem das migrations — antes do deploy

| Ordem | Migration | Observação |
|---|---|---|
| 1 | `058_whatsapp_instancias_grupos.sql` | |
| 2 | `059_campanhas_grupos.sql` | |
| 3 | `060_roteiros_templates.sql` | cria `templates_mensagem` antes das FKs |
| 4 | `062_integracoes.sql` | tem backfill a partir de `shopee_integrations` |
| 5 | `063_campanha_links_eventos.sql` | |
| 6 | `065_campanha_anuncios_leads.sql` | o UNIQUE falha se o mesmo `campaign_id` estiver em duas campanhas de grupos — cheque duplicatas antes |
| 7 | `066_monitoramentos.sql` | |
| 8 | `067_blacklist_e_conexao_externa.sql` | **faltava no runbook antigo** |
| 9 | `074_whatsapp_grupo_ativado.sql` | toggle "Ativo" da usuária; tem backfill |
| 10 | `079_campanha_numeros_limite_identificador.sql` | `campanha_numeros` (+ backfill), `limite_participantes`, `identificador`/`identificador_tipo` |

`061` e `064` são de **pg_cron** e ficam de fora desta etapa — ver 3.4.

### 3.2 Variáveis de ambiente no Coolify de produção

Sem elas o módulo sobe e falha em runtime, não no boot.

```
WAHA_URL=                 # servidor WAHA de produção
WAHA_API_KEY=
WAHA_SESSAO_RESUMO=       # sessão global do resumo diário
WAHA_WEBHOOK_TOKEN=       # HMAC do webhook
WAHA_WEBHOOK_URL=         # base pública — NUNCA derivar de url_for (301 em http, falha calada)
WHATSAPP_HASH_SALT=       # ver 3.7; defina ANTES do primeiro evento de participante
OPENAI_API_KEY=           # variações por IA — chave NOVA (a antiga vazou)
```

Com default seguro, só mexa com motivo: `WHATSAPP_MAX_INSTANCIAS_GLOBAL=60`,
`WHATSAPP_TETO_POR_INSTANCIA=80`, `WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA=5000`,
`WHATSAPP_GRUPO_PAUSA_MIN_S=8` / `MAX_S=20`, `WHATSAPP_RODADA_TAMANHO=2`,
`WHATSAPP_FATIA_ORCAMENTO_S=900`, `MONITORAMENTO_RETENCAO_DIAS=30`,
`CONEXAO_CONVITE_MINUTOS=15`.

### 3.3 Infra WAHA

Servidor WAHA **próprio de produção** (engine GOWS), sessões persistidas em
PostgreSQL. Runbook: `docs/whatsapp-waha.md`.

⚠️ **Nunca compartilhe o servidor WAHA entre hml e produção.** O nome da sessão
é `mkd{ref4}u{user_id}x{hex4}` e o `ref4` vem do projeto Supabase, então não há
colisão de nome — mas os dois bancos agendando contra o mesmo WAHA fazem a
afiliada receber tudo em duplicidade.

### 3.4 Os dois crons — o ponto mais perigoso

`061` (roteiros, a cada 5 min), `064` (snapshot diário) e `069` (saúde de proxy,
horário) agendam `pg_net` **no banco**. Hoje os três estão em **homologação**.

**Aplicar em produção e desagendar em homologação têm que ser o mesmo ato.**

```sql
-- em HOMOLOGAÇÃO, no mesmo momento em que produção passa a agendar:
SELECT cron.unschedule(jobid) FROM cron.job
 WHERE command LIKE '%internal/cron/roteiros%'
    OR command LIKE '%internal/cron/grupos-snapshot%'
    OR command LIKE '%internal/cron/proxy-health%';   -- 069, entrou em 27/08
```

Foi um cron rodando 24×/dia que derrubou o banco compartilhado em 20/07.

### 3.5 Frontend — o gating são **cinco** arquivos, não quatro

| Arquivo | O que esconde |
|---|---|
| `src/app/routes/app-routes.tsx:208` | as rotas |
| `src/shared/config/dashboard-menu.ts:62` | itens `hmlOnly` da sidebar |
| `src/features/dashboard/pages/Configuracoes.tsx` | abas Números / Envio / Bloqueios / Resumo |
| `src/features/dashboard/components/ResumoDeGruposCard.tsx:70` | bloco no Dashboard |
| `src/features/dashboard/pages/Campanhas.tsx:197,547` | selo e vínculo na tela de Anúncios |

> 🔴 **A armadilha:** o bloco `!isProductionHost()` de `app-routes.tsx` contém
> **as rotas do Instagram E as dos Grupos**. Removê-lo para liberar Grupos
> **libera o Instagram junto** — sem App Review, contra tabelas sem policy e sem
> o cron de renovação de token. Ao liberar Grupos, **separe o bloco** e mantenha
> `/dashboard/automacoes*` gated. O mesmo vale para `showInstagram` em
> `Configuracoes.tsx`.

Comparação por **igualdade exata** — nunca `.includes()`:
`hml.marketdash.com.br` contém `marketdash.com.br` como substring.

### 3.6 Rotas públicas novas e o proxy do frontend

O backend passa a servir **fora do `/api/v1`**:

| Rota | Para quê |
|---|---|
| `GET /g/{slug}` | link de entrada — escolhe o grupo, registra o clique, dispara o pixel |
| `GET /g/preview/{slug}` | prévia de teste (`is_teste=true`, fora das métricas) |
| `GET /conectar/{token}` | página de pareamento por link externo (item 18) |
| `GET /api/conectar/{token}/qr` | polling do QR dessa página |

O `nginx.conf` do frontend proxya as quatro. **Corrigido em 26/08:** o host de
destino era fixo em `api.marketdash.com.br`, o que fazia **homologação servir
essas páginas a partir da API de produção**. Agora um `map $host $api_upstream`
espelha o `API_BY_HOST` de `src/core/config/api.config.ts`.

O `proxy_pass` com variável resolve DNS em **runtime** e exige `resolver`.
Usamos DNS público (`1.1.1.1`/`8.8.8.8`) de propósito: o `127.0.0.11` do Docker
só existe em rede definida pelo usuário e **recusa conexão na bridge padrão** —
verificado; o `/g` inteiro cairia por causa da topologia da rede.

### 3.7 `WHATSAPP_HASH_SALT` — defina antes do primeiro evento

O identificador de participante é `HMAC-SHA256(segredo, jid)`. Sem
`WHATSAPP_HASH_SALT`, o segredo é derivado de `SHOPEE_ENCRYPTION_KEY`. Funciona
— mas **trocar a origem do segredo depois invalida todo hash já gravado**, e
"entraram e ficaram" passa a não casar. Defina explicitamente **antes** do
primeiro evento real.

> A versão anterior usava `sha256(jid + salt)` com o salt vazio em todos os
> ambientes: o espaço inteiro de celulares BR (~1,08 bi) era reversível em ~11
> minutos a 1,5 M hashes/s. A política de privacidade promete "código
> irreversível" — agora é verdade.

### 3.8 Textos legais

> 🔴 **BLOQUEIO — mudou em 04/09/2026.** A política atual promete que o número de
> quem entra no grupo **nunca é armazenado** (só um código irreversível). Desde a
> migration `079` isso **deixou de ser verdade**: `grupo_eventos.identificador`
> guarda o telefone, porque exportação de lead com hash não serve para nada.
>
> A política **precisa ser atualizada antes do merge `develop→main`** — não
> depois. O que ela precisa passar a dizer:
> - guardamos o identificador de quem **entra em grupo da campanha** (telefone
>   ou, quando a pessoa oculta o número, um id opaco do WhatsApp);
> - para quê: a afiliada exportar os contatos dos próprios grupos;
> - por quanto tempo, e como pedir remoção.
>
> O hash continua existindo ao lado, para casar entrada com saída.

- **Política de privacidade**: a seção 7 já cobre o módulo, inclusive o
  monitoramento. Ela afirma que, por padrão, não recebemos conteúdo de mensagem
  — o que depende de a sessão nascer sem o evento `message` (validado contra o
  WAHA real em 26/08). Reler antes de publicar.
- **Termos de uso**: revisar o uso do número da afiliada e o risco de restrição
  pelo WhatsApp.

### 3.9 Rollback

Nada é destrutivo; as tabelas ficam.

```sql
-- parar todo envio agendado, sem redeploy
UPDATE roteiro_execucoes SET status='pausada' WHERE status IN ('agendada','enviando');
-- cortar na origem
SELECT cron.unschedule(jobid) FROM cron.job
 WHERE command LIKE '%internal/cron/roteiros%'
    OR command LIKE '%internal/cron/grupos-snapshot%';
```

No frontend, repor o `!isProductionHost()` nos cinco pontos esconde o módulo
sem tocar em dado.

---

## 4. Instagram

### 4.1 O que já está em produção — e o que isso significa

As três tabelas existem, criadas pelo **boot**, não pela `052` (ver seção 0).
Faltam **as policies** e **o cron de renovação de token**.

A UI está **inteiramente escondida em produção** hoje — tanto na `main` quanto
na `develop` (`showInstagram = !isProductionHost()`, e a sidebar filtra
`automacoes`). Então **ninguém em produção conecta uma conta hoje**, e o cron
ausente não está causando dano — mas passaria a causar no dia do lançamento,
porque o token longo do Business Login dura 60 dias e **só pode ser renovado
enquanto está válido**. Vencido, a aluna refaz o login na mão.

### 4.2 Correção pendente, independente do lançamento

Aplicar a `052` em produção cria as policies que faltam (é idempotente — o
`DROP POLICY IF EXISTS` antes de cada `CREATE POLICY` torna a reaplicação
segura). Não muda comportamento da API, que conecta com `BYPASSRLS`; alinha
produção com homologação e devolve a segunda linha de defesa.

### 4.3 Checklist de lançamento

1. [ ] **App Review aprovado** nas três permissões — sem isso só admin/testador
       do app conecta
2. [ ] Aplicar `052` (policies) e `053` (cron) em produção;
       `054`/`055`/`056` são no-op lá, mas rode para conferir
3. [ ] Cadastrar as URLs **de produção** na Meta: redirect, deauthorize,
       data-deletion e webhook
4. [ ] `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `INSTAGRAM_OAUTH_REDIRECT_URI`,
       `INSTAGRAM_WEBHOOK_VERIFY_TOKEN` no Coolify de produção
5. [ ] Separar o gating (ver 3.5) e liberar **só** as rotas do Instagram
6. [ ] Backend primeiro, frontend depois — o frontend chama rotas que precisam existir
7. [ ] Acompanhar 24h:
       `SELECT dm_status, count(*) FROM instagram_events WHERE processed_at > now() - interval '24 hours' GROUP BY 1;`

Detalhe de cada passo: `docs/AUTOMACAO_INSTAGRAM.md` §8 e §9.

### 4.4 Rollback

```sql
UPDATE instagram_automations SET status='pausada' WHERE status='ativa';
SELECT cron.unschedule('instagram-token-refresh-diario');
```

`INSTAGRAM_DM_FORMATO=texto` no Coolify + restart volta o direct ao formato sem
botão. Para cortar na origem: remover a assinatura do campo `comments` no painel
da Meta.

---

## 5. Ordem de execução no dia

0. **Ler a seção 8** — é o inventário fechado (migrations, arquivos, envs)
1. **Medir** produção com o SQL da seção 1 — não confie nesta tabela
2. **Listar** `git log --oneline main..develop` nos dois repos e decidir item a item
   (49 e 33 commits em 31/08 — ver seção 2 e 8.5)
3. **Aplicar** `058→059→060→062→063→065→066→067→068→070` em produção
   (**sem** `061`/`064`/`069`, que são de pg_cron)
4. **Conferir** RLS e policies com o SQL da seção 1
5. **Definir** as variáveis do Coolify (3.2) e subir o WAHA de produção (3.3)
6. **Merge do backend** `develop→main` → deploy → **`/health` 200**
7. **Confirmar que o deploy do worker Celery foi junto** — já ficou semanas com
   código velho porque o CI só deployava a API e um `|| echo` mascarava a falha
8. **Separar o gating** de Instagram e Grupos (3.5) e liberar só o de Grupos
9. **Merge do frontend** → deploy
10. **Crons**: agendar `061`/`064`/`069` em produção e **desagendar em hml no mesmo ato**
11. Observar 48h (seção 6)

> **CI verde ≠ deployado.** E o `tsc --noEmit` do CI **não valida `src/`** — a
> verificação real é `npx tsc -b`, com baseline de **26 erros** que não pode subir.

---

## 6. Depois do deploy — 48h

- `sync_runs` com `source='roteiro_execucao'` e `'whatsapp_grupos'` no painel
  `/admin/sincronizacoes` — execução sem rastro é execução que ninguém audita
- Nenhuma linha presa em `enviando` (deveria virar `falhou`, nunca ser reenviada)
- `cron.job` de produção **sem** job duplicado, e hml **sem** os dois de grupos
- RAM do WAHA — a premissa é ~60 MB/sessão no GOWS; o histórico do engine tem
  vazamentos de memória
- Nenhuma sessão órfã no WAHA

---

## 7. Pendências que a promoção não resolve

- **`lead` × `offsite_conversion.fb_pixel_lead` do Meta**: usamos `max()`, que
  nunca infla o número. A relação exata entre os dois `action_type` não está
  fechada na documentação pública e a conta de hml não tem conversão de lead
  para decidir. Revalidar quando uma campanha de grupos com pixel rodar.
- **A suíte do módulo depende do Postgres local (5434)** e **pula em verde**
  onde ele não existe — o CI não protege os invariantes que ela existe para
  proteger.
- **`marcar_todos` do roteiro segue inerte** (decisão consciente: "deixar como
  está por ora").
- **Prints do checklist do Instagram** (`marketdash-frontend/public/instagram/`):
  sem os arquivos a tela mostra só o texto numerado.
- **`OPENAI_API_KEY` nova** — a antiga vazou e continua pendente de revogação.
- **Contagem de linhas das tabelas do Instagram em produção não foi medida**: a
  leitura das credenciais de produção foi bloqueada. Antes de aplicar a `052`,
  vale um `SELECT count(*)` nas três para saber se há dado gravado no período
  sem policy.

---

## 8. Inventário do que falta promover (medido em 31/08/2026)

Esta seção é a **lista fechada**. Se algo não está aqui, não sobe. Nada abaixo
foi executado em produção.

### 8.1 Migrations — 13 arquivos, nenhum em produção

Ordem obrigatória. As três de `pg_cron` (`061`, `064`, `069`) ficam **fora do
lote inicial** e vão só no passo 10 da seção 5, junto com o desagendamento em
homologação.

| # | Arquivo | O que faz | Produção | Homologação |
|---|---|---|---|---|
| 1 | `058_whatsapp_instancias_grupos.sql` | 3 tabelas base do módulo | ausente | OK (RLS) |
| 2 | `059_campanhas_grupos.sql` | `campanhas`, `campanha_grupos` | ausente | OK (RLS) |
| 3 | `060_roteiros_templates.sql` | 7 tabelas — cria `templates_mensagem` antes das FKs | ausente | OK (RLS) |
| 4 | `062_integracoes.sql` | `integracoes` + **backfill** de `shopee_integrations` | ausente | OK (RLS) |
| 5 | `063_campanha_links_eventos.sql` | 4 tabelas de link/evento/snapshot | ausente | OK (RLS) |
| 6 | `065_campanha_anuncios_leads.sql` | `campanha_anuncios` + `campaign_daily_insights.leads` | ausente | OK |
| 7 | `066_monitoramentos.sql` | `monitoramentos`, `monitoramento_capturas` | ausente | OK (RLS) |
| 8 | `067_blacklist_e_conexao_externa.sql` | `conexao_convites` + `blacklist_numeros.numero_mascarado` | ausente | OK (RLS) |
| 9 | `068_whatsapp_proxies.sql` | `whatsapp_proxies` + `whatsapp_instancias.proxy_*` | ausente | OK |
| 10 | `070_whatsapp_instancia_envio_pausado.sql` | `whatsapp_instancias.envio_pausado` + `.pausado_em` | ausente | OK (31/08) |
| 11 | `074_whatsapp_grupo_ativado.sql` | `whatsapp_grupos.ativado` + backfill (campanha **e** monitoramento) | **PENDENTE** | OK (03/09) |
| 12 | `075_facebook_ad_accounts_names.sql` | `facebook_integrations.ad_accounts_names_json` | **PENDENTE** | OK (03/09) |
| 13 | `076_subscription_pending_tier.sql` | `subscriptions.pending_*` (4 colunas) | **PENDENTE** | OK (03/09) |
| — | `061_cron_roteiros.sql` | `roteiros-tick-5min` (a cada 5 min) | ausente | **agendado** |
| — | `064_cron_grupos_snapshot.sql` | `grupos-snapshot-3am-brt` (diário) | ausente | **agendado** |
| — | `069_cron_proxy_health.sql` | `proxy-health-horario` (horário) | ausente | **agendado** |
| — | `077_remove_resumo_blacklist.sql` | **desagenda** `whatsapp-resumo-9am-brt` + DROP de 3 tabelas | **PENDENTE** | OK (03/09) |
| — | `078_shopee_sync_por_usuario.sql` | função `trigger_shopee_sync_user` (só a função — agendamento é manual e diverge) | **PENDENTE** | **PENDENTE** |

> ⚠️ **As `074`–`077` são da rodada de Configurações (03/09).** Aplicadas em
> **homologação em 03/09/2026** e verificadas objeto a objeto (colunas criadas,
> 3 tabelas derrubadas, cron desagendado, API religada limpa). **Em produção
> continuam PENDENTES** — as quatro precisam ir junto com o deploy dessa rodada.
>
> Três são `ALTER TABLE` pura (mesma armadilha da `070`: `create_all` não
> adiciona coluna em tabela existente — o boot-ALTER em `db/base.py` é rede
> extra, não substituto).
>
> ⚠️ **A `078` (04/09) não é da promoção do módulo — é do sync Shopee**, e é a
> única migration deste inventário cujo agendamento **diverge por ambiente**:
> produção quer os 24 `shopee-sync-*` ligados (já religados em 04/09 via
> `cron.alter_job`), homologação quer todos desligados com só a conta do Luiz
> Fernando (user 9) agendada. Por isso o arquivo cria **apenas a função** e
> deixa os dois blocos de `cron.schedule` comentados no rodapé: rodar o arquivo
> inteiro no ambiente errado religaria em hml a cadência horária de 20/07.
>
> Lembrete de execução: **`UPDATE cron.job SET active = ...` não funciona no
> Supabase** (`42501: permission denied for table job`). Use `cron.alter_job()`
> ou recrie pelo nome com `cron.schedule`. E `SELECT` em `cron.job` pode vir
> **vazio em vez de erro** — a RLS filtra por `username = current_user`.

> **A `077` é a única que precisa rodar mesmo em produção, onde o módulo de
> grupos não existe:** ela desagenda o pg_cron `whatsapp-resumo-9am-brt`, que
> **está ativo lá** (`0 12 * * *` = 9h BRT). O código do resumo foi removido
> nesta rodada — sem a `077`, o job passa a bater em 404 todo dia, para sempre.
> Ela também dropa `whatsapp_optins`, `whatsapp_envios` e `blacklist_numeros`:
> em produção **essas tabelas têm dado real de aluna** (opt-ins do resumo), que
> é descartado de propósito junto com a feature — se alguém quiser o histórico,
> exportar **antes**. Em hml eram 1 opt-in e 6 envios de teste.
>
> A `076` é a única que toca `subscriptions` (tabela viva em produção, com
> assinatura de todo mundo) — são 4 `ADD COLUMN` idempotentes, sem reescrita de
> linha. A `074` precisa rodar **antes** do deploy: seu backfill é o que impede
> o toggle de desligar campanha e monitoramento em operação.

> ⚠️ **A `070` é a única `ALTER TABLE` pura do lote, e é a armadilha inversa da
> seção 0.** `create_all` cria tabela nova, mas **não adiciona coluna em tabela
> existente**. Nas outras 9, subir o deploy antes da migration dá tabela sem RLS
> (ruim, silencioso). Na `070`, dá **`UndefinedColumn` em `GET /instancias` para
> toda usuária MAX** — barulhento e imediato. Ela depende da `058`.

### 8.2 Variáveis de ambiente no Coolify de produção

**33 settings** nasceram em `develop` (`git diff main..develop -- app/core/config.py`).
A maioria tem default seguro; a tabela abaixo separa o que **quebra sem valor
explícito** do que só precisa de revisão.

**Obrigatórias — sem elas o módulo sobe e falha em runtime, não no boot:**

```
WAHA_URL=                 # servidor WAHA de PRODUÇÃO (nunca o de hml — ver 3.3)
WAHA_API_KEY=
WAHA_SESSAO_RESUMO=       # sessão global do resumo diário
WAHA_WEBHOOK_TOKEN=       # HMAC do webhook
WAHA_WEBHOOK_URL=         # base pública — NUNCA derivar de url_for (301 em http, falha calada)
WHATSAPP_HASH_SALT=       # ver 3.7; defina ANTES do primeiro evento de participante
OPENAI_API_KEY=           # variações por IA — chave NOVA (a antiga vazou e segue pendente de revogação)
OPENAI_MODELO=            # tem default; fixe para não seguir mudança de default nossa
```

**Do proxy por sessão (`068`/`069`) — o bloco inteiro é novo e não está na 3.2 antiga:**

```
WHATSAPP_PROXY_OBRIGATORIO=false     # deixe FALSE até o pool ter IP — ver o aviso abaixo
WHATSAPP_PROXY_APLICAR_AUTOMATICO=
WHATSAPP_PROXY_MAX_SESSOES=
WHATSAPP_PROXY_COOLDOWN_H=
WHATSAPP_PROXY_FALHAS_DEGRADADO=     # 2
WHATSAPP_PROXY_FALHAS_QUARENTENA=    # 4
WHATSAPP_PROXY_HEALTH_URL=
WHATSAPP_PROXY_HEALTH_TIMEOUT_S=
WHATSAPP_PROXY_GEO_URL=
```

**Com default seguro — só mexa com motivo:** `WHATSAPP_MAX_INSTANCIAS_GLOBAL=60`,
`WHATSAPP_TETO_POR_INSTANCIA=80`, `WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA=5000`,
`WHATSAPP_GRUPO_PAUSA_MIN_S=8` / `MAX_S=20`, `WHATSAPP_GRUPO_JITTER_MIN_S` /
`MAX_S`, `WHATSAPP_RODADA_TAMANHO=2`, `WHATSAPP_FATIA_ORCAMENTO_S=900`,
`MONITORAMENTO_RETENCAO_DIAS=30`, `CONEXAO_CONVITE_MINUTOS=15`.

**Supabase — aditivas, as antigas seguem funcionando** (commits `c36b6d5`/`d924d6c`):
`SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `SUPABASE_JWKS_URL`.
Não são do módulo de grupos e podem subir antes, sozinhas.

**Frontend (build-time, no Coolify do frontend):** nenhuma variável **nova** —
`.env` só mudou o `VITE_API_URL` do ambiente local (commit `de0dd9f`). O
`VITE_SUPABASE_ANON_KEY` já usa a chave publicável nova.

### 8.3 Arquivos e configuração que não são migration nem env

| O quê | Onde | Por que importa na promoção |
|---|---|---|
| **`feature-flags.json` do backend** | `marketdash-backend/feature-flags.json` (versionado) | Está com **`whatsapp_proxy: true`**. O merge liga a flag em produção — e o **pool de IPs nasce vazio lá**. Efeito: toda sessão sai pelo IP do servidor com WARNING no log, igual a hoje em hml. Não quebra, mas **não entregue como se o proxy estivesse protegendo alguém**. Se preferir, suba com `false` e ligue depois de cadastrar os IPs |
| **`feature-flags.json` do frontend** | `marketdash-frontend/feature-flags.json` | Cópia separada e **divergente de propósito** (não tem `whatsapp_proxy`, que é só do backend). Flag lida pelos dois entra **nas duas** |
| **`nginx.conf` do frontend** | +49 linhas | `map $host $api_upstream` para `/g`, `/g/preview`, `/conectar` e `/api/conectar`. Sem ele, essas rotas de hml batem na API de **produção** (era o bug corrigido em `315f2ad`). Vai junto com o deploy do frontend |
| **Servidor WAHA de produção** | infra | Não existe ainda. Ver 3.3 — **nunca** compartilhar com hml |
| **Gating do frontend — 5 arquivos** | ver 3.5 | Separar o bloco de Instagram do de Grupos. Liberar Grupos sem separar **libera o Instagram junto**, sem App Review |
| **Prints do Instagram** | `marketdash-frontend/public/instagram/` | Ausentes; a tela mostra só o texto numerado |
| **Textos legais** | política de privacidade §7 e termos | Reler antes de publicar (ver 3.8) |

### 8.4 Rotas públicas novas (fora do `/api/v1`)

`GET /g/{slug}`, `GET /g/preview/{slug}`, `GET /conectar/{token}` e
`GET /api/conectar/{token}/qr`. Detalhe e a armadilha do `resolver` do nginx em
**3.6**.

### 8.5 O que NÃO é do módulo e pode subir sozinho

Correções reais que hoje estão presas em `develop` junto com a feature. Se a
promoção do módulo demorar, vale um cherry-pick — **decisão do João, item a item**:

| Commit | O que corrige |
|---|---|
| `457a478` | insights do Meta vinham vazios: gasto sumia e o lucro aparecia positivo num dia de prejuízo |
| `1e300aa` | CSV sumia quando o broker estava fora do ar |
| `e8aba4d` / `caea8e9` | token Meta expirado deslogava a usuária (401 → 409) |
| `1bd8d6a` | runs presos em `running` para sempre |
| `4c72e6f` | backfill tolera ausência da coluna `leads` em produção |
| `c36b6d5` / `d924d6c` | chaves novas do Supabase (aditivo) |
| *(04/09, ainda sem commit próprio — `webhook_helpers`, `kiwify`, `cakto`)* | estorno do pedido antigo renomeava a conta de volta pelo CPF: quem recomprava corrigindo um e-mail errado ficava com a assinatura em uma conta e o login em outra, e via "Assinatura Necessária" depois de pagar. Sem migration; o dado da aluna afetada já foi corrigido em produção, o **código não** |

### 8.6 O que a rodada de 31/08 acrescentou

Card por número na aba Dispositivos, **renomear** e **pausar o envio**:

- **Migration:** `070` (única nova).
- **Backend:** `PATCH /api/v1/whatsapp/instancias/{id}`; `envio_pausado` em
  `InstanciaOut`; filtro da pausa em `roteiro_envio_service._instancias_elegiveis`
  e `grupo_evento_service._cliente_do_grupo`.
- **Frontend:** `DispositivoCard`, `GruposDoDispositivo` e
  `GerenciarDispositivoModal` (novos) + `NumerosSection` reescrita.
- **Env:** **nenhuma**.
- **Gating:** nenhum ponto novo — vive dentro da aba Números, já coberta pelos
  5 arquivos de 3.5.

