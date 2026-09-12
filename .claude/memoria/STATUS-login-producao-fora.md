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
