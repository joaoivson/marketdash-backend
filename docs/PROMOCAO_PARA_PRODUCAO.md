# Promoção para produção — Grupos de WhatsApp e Instagram

> **Nada deste documento foi executado.** Por decisão do João (25/08/2026), tudo
> vive em `develop`/homologação até ser homologado. Estado do banco **medido em
> 26/08/2026**, não lembrado — a seção 1 traz o script para remedir, porque nota
> sobre estado de banco apodrece.

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

## 1. Estado medido em 26/08/2026

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

Produção está **limpa**: nenhuma tabela do módulo existe lá. É o cenário bom.

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
    OR (table_name='user_settings'           AND column_name='whatsapp_envio_config');

-- crons agendados
SELECT jobname, schedule FROM cron.job ORDER BY jobid;
```

**Toda migration daqui é idempotente e aditiva** — `IF NOT EXISTS` em tudo e
`DROP POLICY IF EXISTS` antes de cada `CREATE POLICY` (conferido nas dez).
Reaplicar é seguro. Nenhuma tem `DROP TABLE`/`DROP COLUMN`/`TRUNCATE`.

---

## 2. O que mais vai no merge — leia antes de aceitar

"Subir o módulo de grupos" **não autoriza** subir o backlog de `develop` junto.
São **31 commits no backend** e **16 no frontend**. O que **não** é do módulo:

| Commit | O que é | Decisão |
|---|---|---|
| `1e300aa` | fix: CSV não some quando o broker está fora do ar | correção real, independente do módulo |
| `e8aba4d` / `caea8e9` | fix: token Meta expirado deslogava a usuária (401 → 409) | correção real, independente |
| `c36b6d5` / `d924d6c` | chaves novas do Supabase (`sb_publishable_`/`sb_secret_`) | aditivo; as antigas seguem funcionando |
| `f294cf7` | `CHANGELOG.md` versionado | doc |

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

`061` (roteiros, a cada 5min) e `064` (snapshot diário) agendam `pg_net` **no
banco**. Hoje estão em **homologação**.

**Aplicar em produção e desagendar em homologação têm que ser o mesmo ato.**

```sql
-- em HOMOLOGAÇÃO, no mesmo momento em que produção passa a agendar:
SELECT cron.unschedule(jobid) FROM cron.job
 WHERE command LIKE '%internal/cron/roteiros%'
    OR command LIKE '%internal/cron/grupos-snapshot%';
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

1. **Medir** produção com o SQL da seção 1 — não confie nesta tabela
2. **Listar** `git log --oneline main..develop` nos dois repos e decidir item a item
3. **Aplicar** `058→060→062→063→065→066→067` em produção (**sem** `061`/`064`)
4. **Conferir** RLS e policies com o SQL da seção 1
5. **Definir** as variáveis do Coolify (3.2) e subir o WAHA de produção (3.3)
6. **Merge do backend** `develop→main` → deploy → **`/health` 200**
7. **Confirmar que o deploy do worker Celery foi junto** — já ficou semanas com
   código velho porque o CI só deployava a API e um `|| echo` mascarava a falha
8. **Separar o gating** de Instagram e Grupos (3.5) e liberar só o de Grupos
9. **Merge do frontend** → deploy
10. **Crons**: agendar `061`/`064` em produção e **desagendar em hml no mesmo ato**
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
