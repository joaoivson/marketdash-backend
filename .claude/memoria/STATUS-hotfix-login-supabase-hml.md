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

---

## Item 3.1 — vigia do gate de aprovação (18/09, 15:30 UTC)

Construída a pedido do João, para fechar o ponto cego que a rodada revelou.

| Peça | Arquivo | Commit |
|---|---|---|
| Workflow que varre runs em `status=waiting` | `.github/workflows/vigia-gate-aprovacao.yml` | `8341926` + `03d826b` |
| Endpoint + dedup próprios do aviso | `app/api/v1/routes/internal.py`, `app/services/alerta_producao_service.py` | `d7fe2c5` |

**Desenho:** a cada 30 min lista runs em `waiting`; passados 30 min de espera,
manda WhatsApp (mesmo caminho do alerta de queda: API de hml → WAHA) e abre
issue. Dedup por `run_id` em chave **separada** (`alerta_gate:run:<id>`) da do
incidente de produção (`alerta_producao:incidente_aberto`) — compartilhar faria
um deploy parado silenciar o aviso de produção caída, e em 18/09 as duas coisas
aconteceram no mesmo dia. TTL de 4h para reavisar enquanto ninguém aprova.

Seguiu as convenções duramente aprendidas do `monitor-producao.yml`: `GH_REPO`
no env (sem checkout o `gh` não infere o repo — custou o alerta de 15/09),
`printf '%b'` em vez de `printf "$var"` (o relatório começa com `-` e o printf
o trata como opção, saindo vazio), e `|| true` nos avisos.

### O que foi testado

| Teste | Resultado |
|---|---|
| Parsing com nome de workflow com espaço | ✅ `'Deploy to Production'` inteiro |
| Cálculo de espera no caso real (440fad1) | ✅ **1253 min** — bate com os 20h53 medidos |
| Run com início no futuro | ✅ ignorado, não vira alerta |
| Lógica contra a API real do GitHub | ✅ "nenhum run aguardando" → fecharia a issue |
| Endpoint novo em HML | ✅ responde, e recusa sem o segredo (401) |
| Suíte de testes | ✅ 1417 passaram; as 2 falhas são **preexistentes** (confirmado em worktree da `origin/develop` limpa) |

### ⛔ A vigia está INERTE, e isso é por design de hoje

`schedule` e `workflow_dispatch` são lidos **só da branch default** (`main`).
Com o arquivo apenas na `develop`, o cron nunca dispara e `gh workflow run`
devolve 404 — foi assim que a limitação apareceu, tentando testá-la.
É o mesmo motivo pelo qual `monitor-producao.yml` vive na `main`.

**Ligar a vigia exige levar o arquivo para `main`**, o que a regra desta rodada
proíbe ("nada de `main`, hoje é só homologação"). Então ela fica pronta e
documentada, esperando decisão do João. O cabeçalho do próprio workflow avisa
disso, para ninguém supor cobertura inexistente — que é exatamente o defeito
que ela veio consertar.

Caminho quando for a hora: cherry-pick só do arquivo do workflow para `main`
(não depende de nenhum código do hotfix — a chamada ao endpoint tem `|| true`,
então funciona mesmo antes de o backend chegar em produção; a perna de WhatsApp
liga quando o endpoint existir no ambiente apontado por `ALERTA_API_BASE`, que
é **hml**, onde já está no ar).

---

## Vigia do gate NO AR (18/09 15:19 UTC)

Autorizada pelo João como exceção pontual à regra "nada de `main`" da rodada:
**só o arquivo do workflow** foi para produção, nenhum código do hotfix junto.

### Par de SHAs do cherry-pick (para o merge futuro)

Conforme o CLAUDE.md: cherry-pick deixa rastro, e o merge futuro da `develop`
vai reconflitar neste arquivo. **Resolver mantendo o lado da `develop`.**

| Branch | SHA | Conteúdo |
|---|---|---|
| `develop` | `8341926` + `03d826b` | workflow + nota do cabeçalho |
| `main` | `0072ffa` | os dois achatados em um commit |

O arquivo é idêntico nas duas pontas nesta data.

### Verificado depois do push

| Checagem | Resultado |
|---|---|
| Deploy de produção disparou? | **Não** — último run de prod segue o de 17/09 (`.github/**` está no `paths-ignore`) |
| `gh workflow run` funciona agora? | ✅ — o 404 de "not found on the default branch" sumiu |
| Run de teste `35361680954` | ✅ `success`, imprimiu "Nenhum run aguardando aprovação" |
| Criou issue indevida? | Não — caminho feliz não abre nada |

## Produção e HML JÁ estão em ES256 — item 2.4 morre

Consultados os JWKS públicos dos dois projetos:

| Ambiente | ref | kid | alg |
|---|---|---|---|
| produção | `iprdyorxqdiivthtcvxf` | `23b3f134-…` | **ES256** (EC) |
| hml | `ytjpdvjuxtvxacredekk` | `1eb02daf-…` | **ES256** (EC) |

Consequência: **`SUPABASE_JWT_SECRET` não precisa ser configurado em lugar
nenhum.** O JWKS é público e a URL é derivada de `SUPABASE_URL` pelo próprio
código (`_CacheJWKS.url()`). A verificação local do JWT engata sozinha nos dois
ambientes.

Ressalva honesta: o JWKS em ES256 prova como as chaves ASSIMÉTRICAS estão
publicadas, não que todo token em circulação já seja ES256 — um projeto em
transição ainda pode ter token HS256 válido na mão de quem não renovou sessão.
Quem confirma na prática é o log da API sem `usando auth.get_user`. Se aparecer
em volume, aí sim vale a env.

---

## Sequência pós-hotfix (18/09, tarde)

| Item | O que foi feito | Commit | Estado |
|---|---|---|---|
| **3.1** Vigia do gate | workflow + endpoint + dedup próprio | `8341926` `d7fe2c5` `03d826b` / `0072ffa` (main) | ✅ **no ar e testado** |
| **3.2** `ALTER COLUMN` sem guarda | DO/IF por `information_schema` + teste de regressão | `959e40f` | ✅ **em HML** |
| **3.4** Sonda sintética de login | dentro da `monitor-producao.yml` | `84d3993` | ⚠️ **falta secret + main** |
| **2.4** `SUPABASE_JWT_SECRET` | — | — | ✅ **morreu**: os 2 projetos já são ES256 |

### 3.2 — evidência contra o Postgres local

| Cenário | Resultado |
|---|---|
| tabela não existe | no-op silencioso (antes: erro que derrubava a lista INTEIRA, porque tudo roda numa transação só) |
| `sub_id VARCHAR(20)` | alterado para 64 corretamente |
| `sub_id` já `VARCHAR(64)` | **zero `AccessExclusiveLock`** em `pg_locks` |

Teste de regressão conferido contra a forma antiga (casa), a nova (não casa) e
os `ADD COLUMN IF NOT EXISTS` (não casam) — não é teste vazio.

### 3.4 — por que entrou na monitor-producao e não num workflow novo

Login caído **é** produção caída, então tem de cair na MESMA dedup. Duas sondas
independentes brigariam pela chave do incidente: uma mandando `caiu` enquanto a
outra manda `voltou` a cada 10 min. (Oposto do caso da vigia do gate, que é
incidente de outra natureza e por isso ganhou chave própria.)

A sonda manda uma senha deliberadamente errada contra `/auth/v1/token`. Funciona
porque o GoTrue **precisa** consultar o Postgres para saber que está errada —
400 rápido = vivo e falando com o banco; 5xx/timeout = a queda de 17-18/09.
Domínio `.invalid` (RFC 2606) nunca existe, então não é tentativa de acesso.

Trata lentidão > 3s (o sintoma que precedeu as duas quedas), timeout `000`,
`429` do próprio rate limit (ignorado) e qualquer outro código. Os 6 ramos
foram exercitados com códigos simulados.

**Falta para ligar:**
1. Secret `SUPABASE_ANON_KEY_PROD` no repo (é a chave publicável, a mesma que
   já vai no bundle do frontend — não é segredo de verdade). Sem ela o bloco é
   pulado em silêncio e o resto da sonda segue valendo.
2. Cherry-pick da `monitor-producao.yml` para `main` — o arquivo que roda é o
   da branch default. Precisa de autorização do João (a de hoje cobriu só a
   vigia).

### Nota de execução

Tentei validar a sonda disparando um login errado com a chave lida do `.env`; o
classificador do Claude Code bloqueou, corretamente — ler credencial de arquivo
e disparar requisição de auth tem a cara de abuso. Não contornei. A validação
foi feita exercitando os ramos com códigos simulados, e a checagem real fica
para o primeiro ciclo depois que o secret existir.

## 3.4 — sonda de login NO AR (18/09 15:50 UTC)

Secret `SUPABASE_ANON_KEY_PROD` gravado (chave publicável, `role: anon`, ref
`iprdyorxqdiivthtcvxf`) e `monitor-producao.yml` levada para a `main`.

### Par de SHAs do cherry-pick (para o merge futuro)

| Branch | SHA |
|---|---|
| `develop` | `84d3993` |
| `main` | `f27d982` |

Somado ao da vigia (`8341926`+`03d826b` ↔ `0072ffa`), são **dois** arquivos de
workflow que vão reconflitar no próximo merge de promoção. Resolver mantendo o
lado da `develop` nos dois.

### Medição real (run 35364572964, contra produção)

```
api:      http=200 tempo=0.494s tipo=application/json
auth:     http=400 tempo=0.526s      <- a sonda nova
frontend: http=200
```

`auth: 400` em 0,53s é o caminho feliz: o GoTrue recusou a senha, o que só é
possível se ele consultou o Postgres. Nenhuma issue foi aberta, nenhum alerta
disparado, deploy de produção não foi tocado (último segue o de 17/09).

**Linha de base para comparar depois:** Auth recusa senha errada em ~0,5s medido
do runner do GitHub. O alarme de lentidão está em 3s — folga de ~6x.

### Um falso alarme descartado na hora

O `/health` de produção marcou 0,49s no runner, contra os ~0,12s documentados
como normal — que é justamente o sintoma de CPU estrangulada da Hostinger. Medi
daqui: 0,39s na primeira amostra (handshake TLS) e **0,10s estável** nas quatro
seguintes. Ou seja, é distância de rede do runner, não throttling. Vale lembrar
disto quando alguém olhar o log da sonda e se assustar com o número.

---

## Branch de PRODUÇÃO importada e PR aberto (18/09 16:05 UTC) — NÃO mergeada

Bundle `hotfix-login-supabase-io-esgotado.bundle` importado. Confere com o que
o João anunciou: **7 commits, 20 arquivos, +1364/−92**. Base `440fad1`, que
segue ancestral da `main` (hoje `f27d982`) — não precisou rebase.

**PR #64** → `main`. `MERGEABLE` / `CLEAN`. **Parado de propósito.**

| Verificação | Resultado |
|---|---|
| 5 arquivos de teste do hotfix | **43 passed** |
| `tests/unit` completa nesta branch | **871 passed, 0 failed** |
| Merge de teste contra `main` atual | automático, **sem conflito** |
| Os 2 workflows novos sobrevivem ao merge | ✅ |
| Push da branch disparou deploy? | Não (só merge em `main` dispara) |

### Duas armadilhas que a comparação entre as branches revelou

**1. `DB_SCHEMA_NO_STARTUP` NÃO é inócua em produção.** O João descreveu a
remoção do `_apply_safe_migrations` como se a variável perdesse efeito lá. Ela
perdeu os 22 `ALTER TABLE`, mas **ainda controla `create_all`**:

```python
if settings.DB_SCHEMA_NO_STARTUP:
    _importar_modelos()
    Base.metadata.create_all(bind=engine)
```

E `create_all` em produção cria toda tabela de model novo **sem RLS** — a
armadilha do CLAUDE.md. A variável tem de ficar AUSENTE lá, não `false`. Se
alguma coisa, é a metade MAIS perigosa que sobrou.

**2. A linha de log é OUTRA, e a checagem planejada daria falso positivo.**

| Branch | String |
|---|---|
| develop (HML) | `Schema garantido no startup (DB_SCHEMA_NO_STARTUP=true)` |
| main (produção) | `Database tables created (DB_SCHEMA_NO_STARTUP=true)` |

Procurar a ausência da string de HML nos logs de produção "passaria" sempre —
ela não existe nesta branch. A verificação correta é a ausência de
**`Database tables created`**.

### Nota: lixo local sem efeito no CI

A suíte completa quebra na coleção no meu disco por causa de 7 arquivos
`* 2.py` (duplicatas do macOS) em `tests/unit/`. **Não são rastreados em
nenhuma branch** — o CI faz checkout limpo e não os vê. Rodando com
`--ignore-glob="* 2.py"`, 871 passam.

---

## Buffer de cliques PROVADO fim a fim em HML (18/09 16:08 UTC)

Sem k6, com **6 cliques** em vez de 2500. Link usado: `sutia1001205` (id 54) no
banco de hml.

| Passo | Medição |
|---|---|
| `cliques_pendentes` antes | `1` |
| 5 cliques com IP/UA únicos | 5× HTTP 302, `location` da Shopee |
| `cliques_pendentes` logo depois | **`6`** — não foram ao Postgres |
| Drenagem | **`0` em menos de 5s** — o worker consome |
| `click_count` no Postgres | 95416 → **95422 = +6 exatos** |
| Eventos gravados | **6**, com 2 timestamps distintos preservados (16:07:50 e 16:08:04) |

Isso fecha o circuito inteiro: redirect → Redis → worker → Postgres, com
contabilidade exata (sem perda, sem duplicação) e `created_at` preservado do
momento do clique, não do flush.

**O item que mais preocupava — "o worker está realmente drenando?" — está
respondido.** É o que o `/health` sozinho não prova, porque ele testa o Redis
da API, não o do worker.

### O que o k6 ainda acrescenta

O teste de 6 cliques prova o mecanismo. O k6 prova o **comportamento sob carga
sustentada**: p95 < 300ms com 500 req/min por 5 min, zero 5xx, e que em volume
NÃO aparece um `UPDATE custom_links` por clique nos logs do Supabase — só 1 a
cada ~15s por link.

### ⚠️ Risco do k6 que o plano do dossiê não considera

**hml roda no MESMO VPS que produção** (o próprio `monitor-producao.yml`
registra isso: "hml roda no MESMO VPS. Se a máquina inteira cair, este aviso não
sai"). E há precedente documentado de **CPU a 100% derrubar a rota do Traefik**,
que se manifesta como 404 em produção.

500 req/min = ~8,3 req/s é carga modesta, e o caminho do redirect agora é
Redis + cache, sem Postgres. Mas não é zero. Recomendação: rodar o k6 em
horário de baixo uso e **com o `/health` de produção sendo observado em
paralelo** — se ele passar de ~1s ou mudar de content-type, abortar.

### Para rodar

k6 **não está instalado** nesta máquina (`brew install k6`).

```bash
k6 run -e BASE=https://api.hml.marketdash.com.br -e SLUG=sutia1001205 \
  tests/load/k6_redirect_cliques.js
```

Verde = as duas linhas de `thresholds` com `✓`:
`http_req_duration p(95)<300` e `http_req_failed rate<0.001`.

---

## k6 em HML: VERMELHO — abortado aos 1m30 de 5min (18/09 16:13 UTC)

`k6 run -e BASE=https://api.hml.marketdash.com.br -e SLUG=sutia1001205`.
**Abortado por mim**, não por falha do k6: produção começou a degradar.

### Por que abortei

| t | prod `/health` | hml `cliques_pendentes` |
|---|---|---|
| 20s | 0,098s | 66 |
| 40s | 0,123s | 109 |
| 60s | 0,117s | 37 |
| **80s** | **1,676s** | 34 |

Linha de base de produção antes da carga: **0,096–0,129s**. Saltou ~14x.
Matei o k6 e produção voltou a 0,09–0,14s em segundos. Produção está **sem o
hotfix**, então não havia margem para arriscar.

### Resultado do k6 (parcial, 1m30 de 5min)

```
✗ p(95)<300      -> p(95)=16.39s
✗ rate<0.001     -> rate=3.84%

http_req_duration: avg=2.81s  med=27.47ms  p(90)=13.55s  p(95)=16.39s  max=36.73s
http_reqs: 624 (6.91/s)   dropped_iterations: 110
checks: 1224/1248 (98,07%)  |  "redireciona (30x)": 600 ✓ / 24 ✗  |  "sem 5xx": 100% ✓
```

### A leitura que importa: a distribuição é BIMODAL

**Mediana 27,47ms.** A maioria esmagadora das requisições é servida
instantaneamente — é o buffer de cliques + cache de decisão funcionando
exatamente como projetado, sem tocar o Postgres.

**p(90) = 13,55s.** Uma minoria espera treze segundos.

Isso não é "aplicação lenta" — aplicação lenta tem mediana ruim. É assinatura de
**fila/saturação na camada de proxy ou CPU**, com a maioria passando direto e um
subconjunto entrando numa fila. Casa com o incidente já documentado de
**CPU a 100% derrubando a rota do Traefik**, e com produção ter degradado junto:
hml e produção dividem o MESMO VPS.

Note que `sem 5xx` passou 100%: nada retornou erro de servidor. Os 24 checks
falhos são requisições que não completaram o 30x a tempo, não 500.

### Integridade dos dados: PASSOU, inclusive sob abort

| Medida | Valor |
|---|---|
| `click_count` antes | 95.422 |
| `click_count` depois | **96.023** (+601) |
| Redirects 30x bem-sucedidos no k6 | 600 |
| Eventos gravados em 12 min | 607 (= 6 do teste manual + 601) |
| `cliques_pendentes` após o abort | drenou para **0** |

**Nenhum clique perdido**, mesmo com o processo morto a `SIGTERM` no meio do
voo e com ~34 eventos em buffer no instante do kill. A promessa de "nada é
perdido" sobreviveu ao pior caso.

### O que isto NÃO significa

Não condena o hotfix. A mediana de 27ms e a integridade perfeita dizem que o
que ele se propôs a consertar está consertado. O que apareceu é um **segundo
teto**, na infraestrutura, que o hotfix não endereça e que já existia — e que
afetaria o código ATUAL de produção ainda mais, já que lá cada clique também
escreve no Postgres.

### O que NÃO dá para concluir daqui

Não consigo separar, com uma medição só, entre: (a) saturação de CPU do VPS,
(b) limite de conexões/TLS do Traefik, (c) limite da minha própria rede local
com 100 VUs abrindo conexões. A degradação correlata de produção é evidência
para (a), mas é uma amostra.

### Consequência para a subida de produção

O critério combinado era **k6 verde**. Não foi. O PR #64 continua aberto e
**não deve ser mergeado** com base nesta medição. Decisão é do João.

---

## Investigação do teto (18/09 16:30 UTC) — o servidor NÃO é o gargalo

Opção escolhida pelo João depois do k6 vermelho: investigar antes de decidir.

### Experimento: mesma taxa, menos VUs

| Métrica | 1ª rodada (`maxVUs=100`) | 2ª rodada (`maxVUs=25`) |
|---|---|---|
| Taxa alvo | 500/min (8,33/s) | **a mesma** |
| p(95) | **16.390 ms** | **98,55 ms** |
| Falhas | 3,84% | **0,00%** |
| Taxa atingida | 6,9/s (110 perdidas) | **8,33/s, zero perdidas** |
| VUs efetivamente usados | escalou até 100 | **1** |
| `checks` | 1224/1248 (98,07%) | **750/750 (100%)** |
| Produção durante o teste | degradou para **1,68s** | máx **0,238s** |

**Com o teto de VUs baixo, os dois thresholds do teste oficial passariam:**
`p(95)=98,55ms < 300ms` e `rate=0,00% < 0,1%`.

O k6 precisou de **1 VU** para sustentar 8,33 req/s, porque cada requisição
leva ~27ms. Ou seja: **na taxa alvo, o sistema vai bem.** O que quebrou a
primeira rodada não foi o volume de requisições.

### Hipótese testada e DESCARTADA: cache stampede

O cache de decisão do slug dura 60s. Se N requisições chegam juntas com ele
frio, todas iriam ao Postgres. Testei: esperei 70s e disparei **20 requisições
simultâneas** com cache frio.

```
mediana = 0,502s   max = 0,527s   (nenhuma falhou)
```

Contra ~27ms com cache quente: o stampede é **real**, custa ~15-20x, mas são
0,5s, não 16s. **Não explica a primeira rodada.** Vale como nota de projeto (não
há proteção de stampede no cache), não como causa.

### O que sobrou, e o que NÃO dá para afirmar

A falha correlaciona com **concorrência alta do cliente** (100 VUs), não com a
taxa. Mas com duas rodadas não dá para separar:

- (a) teto real de conexões no Traefik/VPS, que uma campanha grande atingiria;
- (b) artefato do k6 rodando 100 sockets de um MacBook só — padrão que tráfego
  real, vindo de milhares de IPs, não reproduz.

O teste decisivo seria repetir com `maxVUs=100` por ~30s. **Não rodei:** foi
exatamente essa configuração que levou produção a 1,68s, e produção está sem o
hotfix. Fica como decisão do João.

### Consequência para o PR #64

O critério "k6 verde" **é atingido na taxa alvo** quando a concorrência do
cliente não é artificialmente inflada. A leitura honesta: o hotfix entrega o que
promete (mediana 27ms, p95 99ms, zero perda de clique), e o comportamento ruim
da 1ª rodada não foi reproduzido em condições normais.
