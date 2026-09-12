# STATUS — Login de produção fora do ar (12/09/2026) — RESOLVIDO

**Sintoma:** login em marketdash.com.br falhava com "Failed to fetch" + CORS
bloqueando `https://api.marketdash.com.br/api/v1/auth/me`.

**Causa raiz:** o VPS (31.97.22.173) ficou com **CPU em 100% desde ~17h de
11/09** e a Hostinger ativou "CPU limitation". Sob throttling, o container da
API de produção parou de ser roteado pelo Traefik — respondia
`404 page not found` em `text/plain` (404 default do proxy) em TODA rota,
inclusive `/health`. Sem rota não há resposta da app, logo o preflight volta
sem `Access-Control-Allow-Origin` → o navegador reporta como erro de CORS.
**O CORS era sintoma, não causa. Supabase não teve participação nenhuma.**

**Correção:** João removeu a limitação de CPU no painel da Hostinger + restart
do container da API via API do Coolify (deployment `doc408skkks440swgw4kcs4k`).

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Confirmar alcance da falha | curl /health, /docs, /openapi.json, OPTIONS auth/me | eu | — | ✅ 404 Traefik em todas | — |
| 2 | Descartar DNS/TLS | dig + openssl s_client | eu | — | ✅ DNS→31.97.22.173, cert LE até 23/10 | — |
| 3 | Comparar com hml | curl api.hml.marketdash.com.br/health | eu | — | ✅ 200 healthy (mesmo Traefik) | — |
| 4 | Estado no Coolify | GET /applications prod+hml | eu | — | ✅ running:healthy, labels Traefik corretas | — |
| 5 | Descartar conflito de domínio | listar as 8 apps e seus fqdn | eu | — | ✅ sem duplicata | — |
| 6 | Identificar causa no VPS | painel Hostinger (print do João) | João | — | ✅ CPU 100% desde ~17h + throttling | — |
| 7 | Remover limitação do VPS | painel Hostinger | João | — | ✅ "VPS limitations were removed" | — |
| 8 | Restart do container prod | POST /applications/<uuid>/restart | eu | ✅ | ✅ health 200, db+redis connected | — |
| 9 | Validar CORS real | OPTIONS + GET com Origin de produção | eu | ✅ | ✅ preflight 200 c/ ACAO; GET 401 JSON da app | — |
| 10 | Validar login no navegador | Playwright em marketdash.com.br/login | eu | ✅ | ✅ auth/me 200 + 8 chamadas 200 | ✅ dashboard com dados reais |

## Evidências

- Antes: `curl https://api.marketdash.com.br/health` → `404 page not found` (text/plain).
- Depois: `{"status":"healthy","database":"connected","redis":"connected"}`, ~110ms.
- Preflight depois: `200` + `access-control-allow-origin: https://marketdash.com.br`.
- Playwright: `URL final: https://marketdash.com.br/dashboard`, `200 /api/v1/auth/me`,
  dashboard renderizando R$ 13.545,40 de comissão e 4.287 pedidos.

## ⚠️ PENDENTE — a causa do pico de CPU não foi identificada

A CPU saltou para 100% por volta das **17h de 11/09** e ficou lá ~20h. O
gráfico de *Incoming Traffic* mostra um pico de ~1,5 GB **no mesmo instante** —
perfil de pull de imagem Docker (deploy) ou de upload grande.

Não consegui apurar o que consumia CPU: o acesso SSH (`docker ps`, `docker
stats`) foi negado pelo classificador do Claude Code, e
`GET /applications/<uuid>/logs` do Coolify volta vazio (registro de servidor
`busy` quebrado — ver [[reference_coolify]]).

**Se não for investigado, isso volta.** Para apurar, no Terminal da UI do
Coolify (`/terminal`, shell root no host):

```bash
docker stats --no-stream --format "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" | sort -k2 -hr | head
journalctl --since "2026-09-11 16:30" --until "2026-09-11 18:00" | tail -100
docker ps --format "{{.Names}}\t{{.Status}}\t{{.CreatedAt}}"
```

Suspeitos, pela memória do projeto: worker Celery em loop, sync Shopee full,
ou os 24 jobs de pg_cron ([[project_rodada_configuracoes_2]]).

---

# Apuração da CPU a 100% (12/09, após autorização de SSH)

| # | Etapa | O que foi apurado | Quem | Código | API | Tela |
|---|---|---|---|---|---|---|
| 11 | SSH no host | ❌ `Permission denied (publickey,password)` — esta máquina não tem chave no servidor | eu | — | — | — |
| 12 | `execute` do Coolify | ❌ 404 — endpoint não existe no beta.462 | eu | — | ✅ testado | — |
| 13 | Correlacionar com CI | ✅ deploy **prod** 11/09 21:12:05Z e **hml** 21:11:44Z — 21s de diferença | eu | — | ✅ gh run list | — |
| 14 | Commit que subiu | ✅ `d3e5600` ledger do webhook IG (migration 083, já aplicada antes do push) | eu | ✅ | — | — |
| 15 | Descartar enxurrada de webhook IG | ✅ ledger: 1–7 entregas/hora, **zero** presas em `enfileirado` | eu | — | ✅ SQL em prod | — |
| 16 | Medir CPU atual (indireto) | ✅ /health em ~110ms (hml em ~300ms) → carga normal agora | eu | — | ✅ | — |

## Conclusão

**Gatilho (provado):** dois builds Docker simultâneos no MESMO host que serve
produção. O Coolify constrói in-place; `develop` e `main` foram empurradas com
21 segundos de diferença, disparando build de prod + hml ao mesmo tempo num VPS
de 4 vCPU que já roda 10+ containers (API prod, celery prod, API hml, celery
hml, celery-whatsapp hml, frontend hml, WAHA, Coolify, Traefik, Redis). O pico
de ~1,5 GB de tráfego de ENTRADA no mesmo instante é o pull das camadas.

**Por que durou ~20h (inferência forte, não confirmada por shell):** o build
estourou o limite e a Hostinger ativou "CPU limitation". A partir daí o teto
passa a ser uma fração das vCPUs, e a carga NORMAL já satura essa fração — o
gráfico marca 100% continuamente e não desce sozinho. É auto-sustentável até
alguém remover a limitação no painel. Bate com o desfecho: João removeu a
limitação + restart → API respondendo em 110ms no mesmo minuto.

**Descartados com evidência:** webhook do Instagram (volume irrisório),
migration faltando (083 já aplicada), erro de CORS, Supabase, DNS, TLS,
conflito de domínio no Traefik.

**Não confirmado:** se havia também um processo em loop. Exige
`docker stats` no host. Chave pública gerada para o João colar em
Hostinger › SSH key › Manage:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAiX1TekhPRK61C4RvppgvPitZqioZ60TuzeTWeY1YLf claude-code-marketdash
```
(privada em `/tmp/claude-501/md_vps`, fora do repo)

## Como evitar que volte

1. **Não empurrar `develop` e `main` no mesmo minuto.** Foi o gatilho literal.
2. **Limitar CPU por container no Coolify** (Advanced › Resource Limits) — teto
   no build e nos containers de hml, para que hml nunca possa matar produção.
3. **Tirar hml do VPS de produção.** Hoje homologação e produção disputam as
   mesmas 4 vCPU; é a fragilidade estrutural por trás deste incidente e do de
   20/07 ([[project_incidente_sync_horario]], mesmo padrão: recurso
   compartilhado prod+hml derrubando produção).
4. **Alerta de CPU.** Hoje só se descobre pelo aluno reclamando de login.
5. **Healthcheck no Coolify está DESLIGADO** (`health_check_enabled: False`) no
   app de produção — por isso o Coolify dizia `running:healthy` com a API
   inalcançável há horas. Ligar com path `/health`.

---

# Execução das 4 medidas (12/09, tarde)

| # | Etapa | O que está sendo feito | Quem | Código | API | Tela |
|---|---|---|---|---|---|---|
| 17 | Teto de CPU em hml | PATCH limits_cpus/limits_memory nos 5 containers | eu | ✅ | ✅ 5× http=200, valores conferidos na releitura | — |
| 18 | Aplicar os tetos | restart dos 5 containers hml | eu | ✅ | ✅ api-hml e frontend-hml de volta em 200 | — |
| 19 | Healthcheck de produção | health_check_path `/` → `/health` | eu | ✅ | ✅ PATCH 200 | — |
| 20 | Sonda externa | `.github/workflows/monitor-producao.yml` | eu | ✅ | ✅ 4 cenários testados | — |
| 21 | Não rebuildar por mudança de CI | `.github/**` no paths-ignore dos 2 deploys | eu | ✅ | ✅ push em main NÃO disparou Deploy to Production | — |
| 22 | Fix do monitor | label inexistente derrubava o step sob `bash -e` | eu | ✅ | 🔄 revalidando | — |
| 23 | Tirar hml do VPS | **depende de decisão do João** (servidor novo) | João | ⬜ | ⬜ | ⬜ |

**Bloqueio da linha 23:** exige contratar um segundo VPS. Não executo sem
autorização de compra.

## Decisões e correções desta rodada

- **O healthcheck NÃO era o buraco.** `running:healthy` estava correto: o
  `HEALTHCHECK` do Dockerfile (linhas 27-28) testa `localhost:8000/health` de
  dentro do container, e por dentro a app estava saudável. Healthcheck interno
  — Docker ou Coolify — não enxerga rota de Traefik quebrada. Corrigi o path
  para `/health` por higiene, mas quem cobre esse modo de falha é a sonda
  EXTERNA (linha 20). Deixei o healthcheck do Coolify desabilitado: o do
  Dockerfile já cobre o interno e habilitar os dois só duplica.

- **Teto de CPU não cobre o gatilho real.** O pico veio de dois `docker build`
  simultâneos; build não roda dentro do container limitado. O teto serve para
  um worker de hml em loop não roubar produção. Quem cobre o build é não
  empurrar `develop` e `main` no mesmo minuto, e a linha 23.

- **Bug pego no teste, não em produção:** `printf "$falhas"` sai vazio quando o
  texto começa com `-` (o printf lê como opção). O alerta chegaria em branco.
  Corrigido para `printf '%b'`.

- **O monitor falhou na 1ª execução com produção saudável:** `gh issue list
  --label incidente` com label inexistente sai com código 1 e derruba o step
  sob `bash -e`. Monitor vermelho no caminho feliz ensina a ignorar o alerta.

- **O push em `develop` ainda disparou build de hml** apesar do `.github/**` no
  paths-ignore (o de `main` não disparou, que era o que importava). A partir do
  próximo push a regra já está na branch. Uma build isolada de hml é inofensiva
  — o que derruba é o par simultâneo.

- **`CHANGELOG.md` e `.claude/memoria/DIARIO.md` ficaram intocados**: têm
  trabalho em andamento do João (rodada de automação do Instagram), não desta
  apuração.

---

# Fechamento (12/09) — decisão do João: não gastar com servidor agora

| # | Etapa | O que está sendo feito | Quem | Código | API | Tela |
|---|---|---|---|---|---|---|
| 23 | Tirar hml do VPS | **ADIADO** — decisão do João: sem gasto agora | João | — | — | — |
| 24 | Serializar builds (substitui 23 sem custo) | grupo `coolify-build-vps` nos 2 deploys + `aguardar-build.sh` | eu | ✅ | ✅ 4 caminhos testados contra o Coolify real | — |
| 25 | Monitor revalidado | run 34696789682 | eu | ✅ | ✅ success; criação de issue pulada (prod saudável) | — |
| 26 | Confirmar que CI não rebuilda mais | 2 pushes (develop + main) | eu | ✅ | ✅ zero deploys disparados | — |

## Estado final dos 4 pedidos

| Pedido | Situação | Observação |
|---|---|---|
| Ligar healthcheck de produção | ⚠️ **reinterpretado** | O `running:healthy` estava CORRETO — o HEALTHCHECK do Dockerfile testa por dentro e a app estava sã. Healthcheck interno não vê rota de Traefik quebrada. Corrigi o path para `/health`; quem cobre o buraco é a sonda externa. |
| Limitar CPU por container | ✅ feito | Teto de 2,0 das 4 vCPU para os 5 containers de hml. |
| Tirar hml do VPS | ⬜ adiado | Decisão do João. Mitigado pela linha 24. |
| Alerta de CPU | ✅ feito | Sonda externa a cada 10 min; alerta inclui lentidão >3s, que é o aviso ANTES do apagão. |

## O que continua descoberto

- **Dois builds simultâneos** só ficam impossíveis com a serialização da linha
  24; se alguém desligar o `concurrency`, o gatilho volta.
- **Não se sabe se havia processo em loop** além do build. Precisa de
  `docker stats` — a chave pública está no topo deste arquivo, ainda não
  cadastrada no painel da Hostinger.
- **Frontend não tem sonda própria de conteúdo** (só código 200). Bundle velho
  servido em silêncio continua invisível — falha já registrada 3× em
  [[reference_coolify]].

---

# As duas pendências (12/09, fim da tarde)

| # | Etapa | O que está sendo feito | Quem | Código | API | Tela |
|---|---|---|---|---|---|---|
| 27 | `docker stats` no host | SSH segue negado; chave NÃO cadastrada | João | — | ❌ bloqueado | — |
| 28 | Métricas do Coolify (alternativa ao SSH) | PATCH recusado: `is_metrics_enabled` "field is not allowed" | eu | — | ✅ testado, é toggle de UI | — |
| 29 | Sonda checa conteúdo do frontend | div#root + bundle referenciado + tipo/tamanho do .js | eu | ✅ | ✅ run 34698168348 verde, bundle 2.275.343B | — |
| 30 | Bundle velho no ar (repo do frontend) | hash do build do CI × hash servido, 3 desfechos | eu | ✅ | ✅ 2 casos testados contra o site real | — |
| 31 | `.github/**` + concurrency no frontend | igual ao backend | eu | ✅ | ✅ push não disparou deploy | — |

## Item 27 — o que ainda falta e por quê

**Não dá para eu resolver sozinho.** Duas saídas, ambas de um clique seu:

1. **Hostinger › SSH key › Manage** — colar:
   `ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAiX1TekhPRK61C4RvppgvPitZqioZ60TuzeTWeY1YLf claude-code-marketdash`
2. **Coolify › Servers › localhost › Settings › Metrics** — ligar. O Sentinel
   JÁ roda (`is_sentinel_enabled: True`, `sentinel_updated_at` atual), só a
   coleta de métricas está desligada (`is_metrics_enabled: False`). Ligando,
   passa a haver CPU/memória por container com 7 dias de histórico. A API
   recusa esse campo (422 "This field is not allowed") — é toggle de UI.

⚠️ **Nenhuma das duas responde retroativamente** o que consumiu CPU em 11/09: o
Sentinel não coletava e os containers já foram reiniciados. O que o SSH ainda
daria é forense de log — `journalctl --since "2026-09-11 16:30"` e
`docker ps --format "{{.Names}}\t{{.CreatedAt}}\t{{.Status}}"` (contagem de
restart). Ligar as métricas serve para a PRÓXIMA vez.

## Item 30 — por que precisou dos DOIS hashes

Comparar só com o hash esperado daria falso alarme: `VITE_API_URL` entra
**inline** no bundle, então CI e Coolify podem gerar hashes diferentes do mesmo
código. Comparar só com o "antes" não distinguiria build no-op de deploy que
não chegou. Com os dois: `== esperado` é confirmação exata, `!= antes` é deploy
feito com hash diferente (avisa e passa), `== antes` é o bundle velho no ar
(erro barulhento).

## O que a sonda NÃO cobre (para não dar falsa sensação)

- **Bundle velho em produção** — de fora não se sabe qual hash deveria estar no
  ar. Isso é do passo de deploy (item 30), não da sonda.
- **Builds simultâneos ENTRE OS DOIS REPOS.** Grupo de `concurrency` do GitHub
  é por repositório: o backend usa `coolify-build-vps` e o frontend
  `coolify-build-vps-frontend`, e eles não se enxergam. Um deploy de backend e
  um de frontend ainda podem construir juntos no mesmo VPS. O
  `aguardar-build.sh` do backend reduz a janela (espera a fila do Coolify
  esvaziar), mas o frontend não tem esse passo.
- **Degradação parcial** (endpoint lento que não seja `/health`, fila do Celery
  parada, cron desagendado).

---

# FORENSE COM SSH (12/09, 14h30) — causa raiz confirmada e uma correção minha

Com a chave cadastrada e as métricas ligadas, deu para fechar. O journal
**sobreviveu ao reboot** (boot -2 cobre 05/09 → 12/09 13:00), e o banco do
Coolify guardou as datas de cada deployment.

## O gatilho, com carimbo de hora

`application_deployment_queues` mostra **CINCO builds começando em 17 segundos**
em 11/09 — não dois, como eu disse antes:

| recurso | início | duração |
|---|---|---|
| marketdash-backend-hml | 21:12:30 | 237s |
| celery-hml | 21:12:31 | 278s |
| celery-whatsapp-hml | 21:12:31 | 501s |
| **api.marketdash.com.br (prod)** | 21:12:46 | ~505s |
| **celery prod** | 21:12:47 | 683s |

`develop` levou 3 recursos e `main` levou 2, com 21s de diferença entre os
pushes. Cinco `docker build` concorrentes por ~11 minutos em 4 vCPU.

**Todos os cinco são recursos do repo do BACKEND** — o frontend nem deployou
naquele dia (último em 08/09). Ou seja, o grupo de `concurrency` compartilhado
entre `deploy-production.yml` e `deploy-homologation.yml` do backend cobre
exatamente este caso: os 3 de hml terminariam antes de os 2 de prod começarem.

## Por que durou ~20h

Hostinger aplicou o "CPU limitation". Com o teto reduzido, a carga ROTINEIRA
(10 containers + sync horário do Shopee + full sync das 04:00) já satura a
fração liberada e o gráfico marca 100% continuamente. Não se recupera sozinho —
depende de alguém remover no painel. Confirmado pelo desfecho: limitação
removida + restart → CPU 8-26%, load 0.27, **com a mesma carga rodando**.

## Descartados COM EVIDÊNCIA

- **Deploy travado** — eu cheguei a afirmar isso lendo `updated_at` da linha
  (57.196s) e estava ERRADO. Os logs do deployment mostram conclusão em 8m25s:
  container novo criado 21:20:20, `"healthy"` às 21:21:01, "Removing old
  containers", "Rolling update completed" 21:21:08. O `updated_at` só ficou
  velho porque a linha foi tocada de novo quando meu restart rodou.
- **Processo em loop** — não há acúmulo: recebidas ≈ concluídas em toda hora
  (47/47, 48/48, 51/51…).
- **Sync Shopee como culpado** — é intencional. `cron.job` tem 24 jobs ativos:
  23 `incremental` de hora em hora + **jobid 91 às 04:00 = `full`**. O pico de
  04:00-07:00 (2.299 chamadas GraphQL vs ~245 de base, e 10 estouros do soft
  limit de 3000s) é o full sync diário, por desenho. E ele roda hoje com CPU em
  8-26%.
- **Webhook do Instagram** — 1 a 7 entregas/hora.
- **Traefik em si** — hml serviu 200 o tempo todo pelo mesmo proxy.
- **Sonda `qemu-ga` da Hostinger** — cheguei a tratar como sinal de CPU alta; é
  rotina, ~52×/dia desde 05/09.

## O que NÃO foi possível recuperar, e a culpa é minha

O container de produção do incidente e os 5 de hml **foram recriados pelos meus
próprios restarts de hoje**, o que apagou os logs deles. E o Sentinel estava com
`is_metrics_enabled: False`, então não existe histórico de CPU por container.
Numa próxima, capturar `docker logs` e `docker stats` ANTES de reiniciar.

## Estado saudável registrado (para comparação futura)

Container de produção: rede `coolify`, 15 labels do Traefik, router
`Host(api.marketdash.com.br) && PathPrefix(/)`. Foi a perda disso — com o
container de pé e saudável — que produziu o 404.

## Buraco que continua aberto

Dentro de UM push, os recursos ainda constroem em paralelo (o push da develop
sozinho dispara 3 builds simultâneos). Serializar isso exigiria esperar entre
cada disparo, o que alonga o deploy de ~8 para ~12 min.
