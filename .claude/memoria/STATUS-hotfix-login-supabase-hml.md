# STATUS — Hotfix login/Supabase IO → Homologação

Rodada de 18/09/2026. Importar `hotfix/login-supabase-io-esgotado-develop`,
testar, abrir PR para `develop`, deploy em HML e validar `/health`.

**Regras da rodada (do João):** nada de `main`, nada de produção, nenhuma
migration rodada à mão, CI vermelho = parar e mostrar o erro.

## Quadro

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Importar branch do bundle | `git fetch` do bundle → branch local `6db8b8a` | eu | ✅ | — | — |
| 2 | Conferir os commits | 8 commits (não 7), 20 arquivos, +1419/−79 | eu | ✅ | — | — |
| 3 | Rodar os 5 testes do hotfix | pytest `.venv312` → **43 passed**, 0 falhas | eu | ✅ | — | — |
| 4 | Push + PR para `develop` | branch no origin + **PR #63**, CLEAN/MERGEABLE | eu | ✅ | — | — |
| 5 | Merge + `deploy-homologation.yml` | merge `cdb5ed0`, run 35357933332 verde, deploy `finished` | eu | ✅ | ✅ | — |
| 6 | Validar `/health` e `/health/live` | SHA bate, `cliques_pendentes: 0`, `/health/live` 200 | eu | ✅ | ✅ | — |

✅ **Etapas 1–6 fechadas em 18/09 às 14:46 UTC.** O João ligou
`DB_SCHEMA_NO_STARTUP=true` nas envs de HML, o merge saiu, o CI fechou verde
(deploy em `finished`) e os dois endpoints responderam o esperado.

### DEPOIS do hotfix em HML (14:46 UTC)

```json
{"status":"healthy","version":"cdb5ed04fb62776a7d39109d981ae9f075c28c09",
 "database":"connected","redis":"connected","cliques_pendentes":0}
```
`/health/live` → **HTTP 200** `{"status":"alive","version":"cdb5ed0…"}`

| Critério | Antes (14:06) | Depois (14:46) |
|---|---|---|
| `version` == SHA do merge | `953014a` (build velho) | `cdb5ed0` ✅ |
| `redis` | connected | connected ✅ |
| `cliques_pendentes` | **ausente** | `0` ✅ |
| `/health/live` | **404** | **200** ✅ |

**⚠️ `DB_SCHEMA_NO_STARTUP` é só de HML/dev.** Em produção fica AUSENTE — ligá-la
lá devolve `create_all` (que cria tabela nova SEM RLS) e 22 `ALTER TABLE` com
lock exclusivo a cada boot, que é a causa nº 3 do próprio incidente. O nome
engana: é "no" do português (*schema NO startup* = faz o DDL ao subir), não o
"no" inglês de negação.

Coluna **Tela** é `—` na rodada: hotfix de backend/infra, sem UI própria. A
validação visual do login fica com o João, junto do k6.

## Linha de base de HML — ANTES do hotfix (18/09 14:06 UTC)

```json
{"status":"healthy","version":"953014ae554e10a36c634b9600d4054cdcba4eb8",
 "environment":"development","database":"connected","redis":"connected"}
```
- `/health/live` → **HTTP 404** (endpoint novo, ainda não subiu)
- **sem** campo `cliques_pendentes`
- `origin/develop` = `65d602b`; HML serve `953014a`

Os 3 pontos são exatamente o que o hotfix muda → "antes" limpo para comparar.

## O que veio na branch (8 commits)

```
6db8b8a chore(dev): docker-compose liga DB_SCHEMA_NO_STARTUP
bda6723 fix(links): isolar as chaves do buffer por banco
ce1e662 fix(health): expor profundidade do buffer de cliques
c57a96d docs: registrar as duas branches do hotfix
af0f362 docs: dossiê do incidente de 18/09
1cade19 fix(startup): sem DDL no boot e healthcheck sem banco
1fdaccd fix(links): clique vai para buffer no Redis
a885941 fix(auth): verificar JWT localmente, sem auth.get_user por requisição
```

## Pontos levantados na revisão (não bloqueiam HML)

1. **8 commits, não 7.** O 8º (`6db8b8a`, docker-compose de dev) provavelmente
   entrou depois da contagem. Nada suspeito.
2. **Revogação de token fica lenta.** Com JWT validado localmente, logout e
   banimento só valem quando o access token expira, em vez de na requisição
   seguinte. É a contrapartida inerente de não consultar o GoTrue por request —
   e é o ponto do fix. O dossiê não lista isso nos "efeitos colaterais aceitos";
   registrado no corpo do PR #63.
3. **`ALTER TABLE whatsapp_grupos ALTER COLUMN sub_id TYPE VARCHAR(64)`**
   (`app/db/base.py:78`) não é `IF NOT EXISTS`. Com a flag ligada em HML, esse
   statement roda **a cada boot**, e `ALTER COLUMN ... TYPE` pode reescrever a
   tabela inteira. O próprio dossiê levanta. Em HML é aceitável (dados
   sintéticos), mas vale corrigir antes de qualquer conversa sobre produção.
4. **Migration 087** (`087_colunas_facebook_pixel_e_ad_accounts.sql`) fica
   pendente de aplicação em produção, já que o DDL saiu do boot. Em HML a flag
   cobre. **Não apliquei nada à mão**, conforme a regra.

## Revisão do caminho de auth (`app/core/supabase_jwt.py`)

Confere: falha fechada em todos os ramos. `alg: none` cai em "algoritmo não
suportado"; não há confusão RS256↔HS256 (o ramo HS256 usa
`SUPABASE_JWT_SECRET`, nunca a chave pública do JWKS); `aud` e `exp` são
verificados; `leeway` de 30s. `auth.get_user` só volta como retaguarda quando
não há chave configurada — deploy seguro mesmo antes da env existir.

---

## Achado 18/09: o gate de aprovação segurou o fix de 17/09 por 21h

O João perguntou se o commit `440fad1`, "parado esperando aprovação desde
ontem", teria resolvido a queda desta manhã. Cronologia real do run
[35254271771](https://github.com/joaoivson/marketdash-backend/actions/runs/35254271771):

| Etapa | Quando (UTC) |
|---|---|
| `440fad1` empurrado para `main` | 17/09 17:41 |
| `Validate Code` ✓ | 17/09 17:41:37 → 17:42:13 |
| `Build e publicar imagens` ✓ | 17/09 17:42:15 → **17:43:04** |
| — **gate de aprovação: 20h50min parado** — | |
| `Deploy em produção` ✓ | **18/09 14:33:32** → 14:35:02 |

**A queda de hoje (7h–8h BRT = 10h–11h UTC) caiu exatamente dentro do buraco.**
Produção rodava o código PRÉ-`440fad1` durante o incidente — o que confirma,
por cronologia e não por inferência, a observação do dossiê ("os logs de 18/09
ainda mostram `click_count=4712` com valor literal").

Confirmado em `curl https://api.marketdash.com.br/health` às 14:37 UTC:
`version: 440fad1…` — ou seja, **produção só recebeu o fix hoje às 14:35**,
minutos antes da pergunta.

### Teria evitado a queda?

O que `440fad1` ataca, e que ESTAVA nos logs de hoje:
- `UPDATE … click_count=62480` em 117s com valor literal → vira incremento
  atômico com `lock_timeout` de 2s: desiste em vez de entrar na fila
- `still waiting for ShareLock` → deixa de acumular
- as 852 conexões abortadas (`SSL EOF`) são em boa parte consequência de
  transação presa segurando conexão; com `lock_timeout` elas liberam rápido

O que `440fad1` **NÃO** ataca, e também estava nos logs:
- **Esgotamento do budget de IO** (`checkpoint … total=217s`, disco caindo para
  5 MB/s de baseline). O fix deixa cada escrita mais barata em TRAVA, não em
  IO — continuam 2 escritas por clique.
- **`auth.get_user` por requisição** (5.895 dos 6.622 requests de Auth). Com
  isso de pé, qualquer lentidão do banco derruba a API inteira, não só o login.
- **Loop de restart com DDL no boot.**

**Veredito honesto: teria amortecido bastante, provavelmente encurtado muito a
queda — mas não dá para afirmar que teria evitado.** Os outros dois problemas
continuariam de pé, e o IO continuaria sob pressão. É exatamente por isso que o
hotfix de hoje existe: ele ataca os três, e mantém o `440fad1` como fallback
quando não há Redis.

### A lição operacional (vale mais que o contrafactual)

Um fix de incidente ATIVO ficou parado 21h num gate, em silêncio, e ninguém foi
avisado. O gate está certo em existir (foi criado porque build no VPS derrubou
produção 2×), mas falta o aviso: "há deploy de produção aguardando aprovação há
N horas". Sem isso, o gate vira um jeito silencioso de não aplicar a correção —
e a segunda queda em 24h aconteceu com o fix pronto, testado e publicado no
GHCR, a um clique de distância.

---

## Correção: `DB_SCHEMA_NO_STARTUP` só tem efeito na API

Pergunta do João (18/09): a variável precisa estar nas 3 apps de HML?
**Não — só na API.** Cadeia verificada no código:

| Elo | Evidência |
|---|---|
| `create_all` roda em um único lugar | `app/db/base.py:137`, dentro de `init_db()` |
| `init_db()` é chamada em um único lugar | `app/main.py:37`, no `@app.on_event("startup")` do FastAPI |
| O worker não passa por ali | `scripts/worker-entrypoint.sh` termina em `exec celery -A app.tasks.celery_app worker`; `celery_app.py` **não importa `app.main`** |
| Nenhum sinal do Celery faz schema | sem `worker_process_init` / `on_after_configure` em `app/tasks/` |

Ou seja: **worker Celery e worker de WhatsApp nunca rodaram `create_all` nem
`_apply_safe_migrations`**, com ou sem a flag. Deixá-la lá é inofensivo, mas não
protege nada.

### Consequência prática para a conferência no Coolify

A linha `Schema garantido no startup (DB_SCHEMA_NO_STARTUP=true)` aparece
**só no log da API de HML**. Procurá-la nos dois workers e não achar é o
comportamento correto — não é sinal de env faltando.

### O dossiê erra neste ponto

`docs/INCIDENTE-2026-09-18-LOGIN-SUPABASE.md` diz que o startup rodava
`create_all` + `ALTER TABLE` "a CADA boot da API **e do worker**" (mesma frase
no docstring de `init_db`). A parte do worker não se sustenta no código. Não
muda o diagnóstico do incidente — o loop de restart era da API, que é quem
tinha o `HEALTHCHECK` batendo em `/health` —, mas muda a conferência: no plano
de produção, não há env de schema a auditar no worker.

---

## Documentação atualizada + redeploy não planejado (18/09 15:00 UTC)

Três commits de documentação:

| Commit | Conteúdo | Disparou deploy? |
|---|---|---|
| `abef833` | dossiê + CHANGELOG: escopo real da flag, gate de 21h, antes/depois de HML, host do k6 | não (`paths-ignore: '**.md'`) |
| `237896f` | docstring de `init_db()` e cabeçalho do k6 — **só comentário** | sim (arquivos `.py`/`.js`) |
| `efea46f` | este quadro | não (`.md`) |

**Erro de execução meu:** eu disse que seguraria o `237896f` para não reiniciar
HML durante a validação, commitei localmente sem empurrar — e em seguida
`git push origin develop` levou a branch inteira, com ele no meio. Branch
empurra todos os commits, não só o último; segurar um commit exige branch
separada ou `push <sha>:develop`.

Consequência real: HML reiniciou uma vez a mais e o SHA mudou de `cdb5ed0` para
`efea46f`. Run `35359421923` fechou verde; os 4 critérios seguem valendo:

```json
{"status":"healthy","version":"efea46fdf27d88519b409732432585b6b6a34279",
 "database":"connected","redis":"connected","cliques_pendentes":0}
```
`/health/live` → **200**

**O SHA a conferir na validação agora é `efea46f`**, não `cdb5ed0`. A diferença
entre os dois é exclusivamente comentário e docstring — nenhuma mudança de
comportamento.
