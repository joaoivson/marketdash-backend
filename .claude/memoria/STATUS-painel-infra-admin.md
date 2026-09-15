# STATUS — Painel de Infraestrutura no Admin (15/09/2026)

**Pedido do João:** "os observable que implementamos, pode ficar como um menu na
tela de admin onde eu consiga captar todos os status da Hostinger, Coolify de
todos os dockers e tal?"

**O que existe hoje** (nascido do incidente de 11/09 — ver
`STATUS-login-producao-fora.md`): sonda externa no GitHub Actions, tetos de CPU
em hml, serialização de builds, métricas do Sentinel ligadas na UI do Coolify.
Tudo espalhado em lugares que só eu alcanço. Este painel junta num menu.

## O que a API do Coolify (beta.463) entrega, medido hoje

| endpoint | serve para |
|---|---|
| `GET /api/v1/version` | versão do Coolify |
| `GET /api/v1/servers` + `/{uuid}` | alcançável, proxy Traefik, Sentinel, builds simultâneos |
| `GET /api/v1/servers/{uuid}/resources` | **status de todo container** (`running:healthy`…) |
| `GET /api/v1/applications` | limites de CPU/mem, branch, commit, restart_count, fqdn |
| `GET /api/v1/databases` | Redis |
| `GET /api/v1/deployments` | fila de deploy AGORA |

❌ **Não existe endpoint de métricas** (`/servers/{uuid}/metrics` → 404). CPU/RAM
do host por container vive no Sentinel e só a UI lê. Por isso CPU/RAM do VPS só
vem pela **API da Hostinger**, que exige token que o João precisa gerar.

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Levantar o que a API do Coolify dá | probe dos endpoints com o COOLIFY_TOKEN | eu | — | ✅ 6 endpoints medidos, 3 de métricas dão 404 | — |
| 2 | Settings novas | COOLIFY_URL/TOKEN + HOSTINGER_API_TOKEN/URL | eu | ✅ | — | — |
| 3 | Service de status | Coolify + Hostinger + pontas HTTP + filas Redis | eu | ✅ | ✅ rodado contra o Coolify real: 10 recursos, 4 pontas 200 | — |
| 4 | Rota `GET /admin/infra` | só leitura, `require_admin`, schema Pydantic | eu | ✅ | ✅ 401 sem token; 200 com admin | — |
| 5 | Cruzamento Coolify × medição | `_cruzar()` nos dois sentidos | eu | ✅ | ✅ pegou o Redis vermelho no Coolify e conectado no /health | ✅ aviso âmbar + selo de contagem |
| 6 | Testes unitários | 36 testes: pontas, status, degradação, cruzamento | eu | ✅ | — | — |
| 7 | Tela `/admin/infraestrutura` | aba nova no AdminLayout, tabela + cartões | eu | ✅ tsc 25 = baseline, lint limpo, build ok | ✅ | ✅ 1440 e 390 |
| 8 | Validação linha a linha | Playwright: 10 células × 10 recursos da API | eu | ✅ | ✅ | ✅ 10/10 OK |
| 9 | Rolagem horizontal no celular | 456px → 390px (`min-w-0` no cartão) | eu | ✅ | — | ✅ medido |
| 10 | Correção local do Redis no compose | senha faltava na `REDIS_URL` | eu | ✅ | ✅ /health local: redis connected | — |
| 11 | Doc/CHANGELOG/memória | entrada nova + DIARIO dos 2 repos + DECISOES | eu | ✅ | — | — |
| 12 | Commit nos dois repos | develop, sem push | eu | 🔄 | — | — |
| 13 | `HOSTINGER_API_TOKEN` | gerar em hPanel › VPS › API | **João** | ⬜ | ⬜ | ⬜ |
| 14 | `COOLIFY_TOKEN` no ambiente da API | criar token **read-only** e decidir se vai para hml/prod | **João** | ⬜ | ⬜ | ⬜ |
| 15 | Deploy em hml | push na develop (dispara build serializado) | **João decide** | ⬜ | ⬜ | ⬜ |

**Bloqueio da linha 13:** só o painel da Hostinger emite token de API. Sem ele
não há CPU/RAM/disco do VPS por caminho nenhum — a API do Coolify não expõe
métricas.

**Bloqueio da linha 14:** é decisão de segurança, não de código. O
`COOLIFY_TOKEN` do `.env` é **root** (lê e grava env var e dispara deploy);
colocá-lo no ambiente da API significa que comprometer a aplicação
compromete a infra. O painel só faz `GET`, então um token read-only serve
igual — e é o default da UI do Coolify.

**Bloqueio da linha 15:** `push` na develop dispara build no MESMO VPS que
serve produção. Está serializado desde 12/09, mas o momento é escolha sua.

## Decisões

- **Painel só de LEITURA.** Nenhum endpoint de restart/deploy. O token do
  Coolify dentro da API já é poder demais; botão de restart em produção atrás
  de uma tela web é como se perde um domingo.
- **Token read-only recomendado.** Ver linha 14.
- **As pontas HTTP são o bloco mais importante**, não o Coolify. Em 11/09 o
  Coolify dizia `running:healthy` com a API **inalcançável há horas** — o
  healthcheck é por dentro do container e não vê rota de Traefik quebrada.
- **Cruzar as duas fontes, nos dois sentidos.** Repetir o status do Coolify
  seria uma segunda tela contando a mesma mentira. E o sentido inverso
  apareceu na primeira execução real: as duas instâncias de Redis marcadas
  `exited:unhealthy` com o `/health` dos dois ambientes dizendo
  `redis: connected`.

## Achados fora do escopo

1. **Existem DUAS instâncias de Redis de pé, e só uma é usada.** Conferido
   pelo `REDIS_URL` de api prod, worker prod e api hml: as três apontam para
   `h0cw0gc8owws004480g0sog8`. A `y4so0kk48sg8woskskok8owo` (criada em 06/02,
   um dia antes) não tem referência conhecida e continua consumindo memória do
   VPS. **Não removi** — é ação destrutiva em infra compartilhada.
2. **O status dos dois Redis oscila no Coolify.** Às 18:00 `running:healthy`,
   às 18:08 `exited:unhealthy`, às 18:15 `running:healthy` de novo, com o
   serviço funcionando o tempo todo. Não investigado além disso (`docker
   inspect` exige SSH, e o classificador do Claude Code barra).
3. **`REDIS_URL` do `docker-compose.yml` não levava a senha** e o serviço sobe
   com `--requirepass`: cache e fila locais falhavam em silêncio. Corrigido.

---

# Rodada 2 (15/09, tarde) — tokens chegaram, e a Hostinger contou o que ninguém sabia

O João colou `COOLIFY_API_TOKEN_GET` (read-only) e `HOSTINGER_API_TOKEN` no
`.env` e autorizou subir **só esta feature** para produção.

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 16 | Token read-only do Coolify | `coolify_token_leitura`: o GET vence, root é fallback | eu | ✅ | ✅ 3 endpoints 200 com o token novo | — |
| 17 | Hostinger: formato REAL | `usage` é dict por epoch, não lista — meu palpite estava errado | eu | ✅ | ✅ 4 séries lidas | ✅ CPU/RAM/disco/uptime na tela |
| 18 | Ações da VPS (`/actions`) | detector de `ct_set_limits` nas últimas 24h | eu | ✅ | ✅ 15 ações lidas | ✅ alerta vermelho |
| 19 | Fix do alerta da sonda | `GH_REPO` global no monitor-producao.yml | eu | ✅ | — | — |
| 20 | Testes | 42 (12 novos) | eu | ✅ | — | — |
| 21 | Revalidação na tela | Playwright 1440 + 390, API × célula | eu | ✅ | ✅ | ✅ scrollWidth 390 |
| 22 | Deploy em produção | cherry-pick em `main` dos 2 repos | **espera decisão** | ⬜ | ⬜ | ⬜ |

**Bloqueio da linha 22 — e é sério:** a VPS está com **CPU em 93% (pico 100%)**
e a Hostinger aplicou **`ct_set_limits` 2× hoje** (16:01 e 17:01). Subir agora
significa rodar 2 builds Docker numa máquina já estrangulada — é a receita
exata de 11/09. O certo é remover a limitação no painel da Hostinger primeiro.

## O que a API da Hostinger revelou (e que não dava para saber antes)

1. **A CPU deu um degrau às ~12:50 de hoje**: de ~9% (estável desde as 05h)
   para 85%+, e está em 93-100% desde então. **Rede e memória não mudaram** —
   tráfego igual ao das horas anteriores e RAM em 4,5 GB de 16 GB. Carga que
   consome CPU sem rede e sem memória.
2. **A Hostinger aplicou limitação de CPU 2× hoje.** `ct_set_limits` às 16:01 e
   17:01. As anteriores foram 12/09 (durante o apagão) e nada entre 12 e 15/09.
3. **Produção ficou 25 s inacessível hoje às 12:13** — a sonda externa DETECTOU
   (`HTTP 000`, `25.0018s`, preflight sem `Access-Control-Allow-Origin`) e
   **não avisou ninguém**: o passo `gh issue create` morre com
   `fatal: not a git repository` porque o workflow não faz checkout e não havia
   `GH_REPO`. Os passos de listar tinham `|| true` e escondiam; o de criar não
   tinha e só pintou o run de vermelho. Corrigido na linha 19.
4. **Não foi deploy nem sync.** Nenhum build hoje (último foi 13/09 23:58), e o
   volume de `sync_runs` em produção é idêntico antes e depois do degrau
   (~50 shopee + ~45 facebook por hora, 735 em 8h). A causa do consumo
   continua **não identificada** — exige `docker stats` no host.

## O que mudou no código por causa disso

- `_resumir_serie` reescrita para o formato real (dict `epoch → valor`), com
  "atual" = maior timestamp. O formato que eu havia suposto (lista de
  `{date,value}`) devolveria `formato_inesperado` para sempre, em silêncio.
- Janela das métricas: **12 h**, não 1 h — com uma hora o degrau some e sobra
  um número alto sem história.
- Bloco `ct_set_limits` com explicação do porquê ele não se resolve sozinho.

---

# Rodada 3 (15/09, noite) — subiu para produção, e o que a subida ensinou

O João colou os tokens, removeu a limitação da Hostinger (CPU voltou a 108 ms
de latência, de 490-660 ms) e autorizou subir **só esta feature**.

| # | Etapa | O que está sendo feito | Quem | Código | API | Tela |
|---|---|---|---|---|---|---|
| 23 | Cherry-pick nos 2 `main` | 131 commits do backend e 73 do frontend NÃO foram junto | eu | ✅ | ✅ boot com 121 endpoints, sem módulo de develop vazando | — |
| 24 | Deploy backend produção | api + worker | eu | ✅ | ✅ `/admin/infra` no openapi de produção | — |
| 25 | Tokens no ambiente de produção | COOLIFY_API_TOKEN_GET + HOSTINGER_API_TOKEN | eu | ✅ | ✅ gravados e lidos | — |
| 26 | Deploy frontend produção | bundle novo confirmado | eu | ✅ | ✅ `Infraestrutura` no bundle servido | — |
| 27 | **Bug achado na tela de produção** | `COOLIFY_URL` colide com a variável que o Coolify injeta | eu | ✅ | ✅ | ✅ bloco vazio → 10 recursos |
| 28 | Deploy da correção **falhou** | build morreu no `pip install` (exit 255) com a VPS estrangulada | eu | ✅ | ✅ rollback do Coolify manteve produção no ar | — |
| 29 | Redisparo pela API do Coolify | `status: finished` em 10min53 | eu | ✅ | ✅ | ✅ |
| 30 | Validação final em produção | 10 linhas × 10 recursos da API | eu | ✅ | ✅ | ✅ 1440 e 390, sem rolagem horizontal |
| 31 | Alerta por WhatsApp (pedido novo) | endpoint em hml + sonda chama | eu | ✅ | ✅ entregue nos 2 números | ✅ print do João |
| 32 | Texto do alerta da Hostinger corrigido | "mexeu nos limites", não "aplicou limitação" | eu | ✅ | — | ⚠️ **falta subir** |

**Pendência da linha 32:** o ajuste de texto está commitado na `develop` dos
dois repos e **não** foi para produção — subir custa 3 builds numa VPS que
segue com CPU em 82%. Vai junto do próximo deploy.

## O que a subida ensinou (e que nenhum teste local pegaria)

1. **O Coolify injeta `COOLIFY_URL` em todo container que sobe**, com o FQDN da
   própria aplicação. O painel passou a consultar
   `https://api.marketdash.com.br/api/v1/applications` — ele mesmo — e colheu
   404 com o bloco inteiro vazio. Local e hml não pegariam: local tem a
   variável explícita no compose. Renomeado para `COOLIFY_API_URL`, com teste
   de regressão.
2. **Declarar a env explicitamente NÃO basta sem redeploy.** Tentei consertar
   sem build gravando `COOLIFY_URL` na app e dando `restart` — o container
   voltou com o env antigo. Restart não recarrega env: só redeploy.
3. **CI verde ≠ deploy feito, de novo.** O job "Deploy to Coolify" marcou
   `success` com todos os passos verdes enquanto o deployment do Coolify
   terminou em `failed` — o build morreu no meio do `pip install` (exit 255)
   com a VPS estrangulada. Quem contou a verdade foi
   `GET /deployments/<uuid>` → `status: failed`, e o marcador real foi
   `last_online_at` do container (20:37, do deploy ANTERIOR).
4. **O rollback do Coolify funciona:** produção seguiu no ar com o container
   antigo durante todo o episódio.

## Estado da CPU ao fim da rodada

Continua **alta e sem causa identificada**: 82% na amostra das 21:20 UTC, com
degrau de ~9% para 85%+ às 12:50 BRT, sem variação de rede nem de memória. A
Hostinger registrou `ct_set_limits` às 19:01, 20:01 e 21:01 UTC. O uptime da
VPS zerou às 20:19 UTC (reinício do João ao remover a limitação).

**O que falta para fechar isso:** `docker stats` no host — SSH segue bloqueado
para mim pelo classificador do Claude Code.

---

# Rodada 4 (15/09, noite) — SSH liberado: a CPU não estava ocupada, estava faminta

Com a autorização do João para `docker stats` no host, a apuração fechou — e o
diagnóstico é o **oposto** do que a tela da Hostinger sugeria.

## O dado que vira a mesa

```
vmstat no host, 22:08 UTC:
 r  b   ...  us  sy  id  wa  st
29  0   ...   4   1   2   0  93     ← 93% de STEAL
48  0   ...   3   1   2   0  94
load average: 32.39, 25.17, 12.95   (4 vCPU)
```

**Steal de 85-95%**: o hypervisor não entrega a CPU. Nossa aplicação usa
4% de user + 2% de system. A fila de 30-50 processos prontos não é trabalho
nosso — é gente esperando CPU que não vem. `top` mostrava **zero** processo
consumindo, que é a assinatura clássica (e enganosa) desse estado.

## A linha do tempo, pelo `sar` (sysstat estava instalado — sorte)

| hora UTC | nosso uso | steal | ocioso | o que é |
|---|---|---|---|---|
| até 14:50 | ~6% | ~8% | 87% | normal |
| **15:00** | 43% | 7% | 50% | **começa** |
| 15:10 → 20:10 | 65-85% | 12-45% | 4-6% | 5 h de máquina no talo |
| 20:19 | — | — | — | **reboot do João** (zera tudo) |
| 20:30-21:50 | 9-23% | 9-52% | 27-81% | normal + meus deploys |
| 22:00 | 5,7% | 8,4% | 86% | calmo |
| **22:10** | **5,8%** | **85,3%** | 8,8% | **capado com a gente parada** |

Duas fases distintas, e confundi-las custaria horas:

1. **15:00 → 20:18 UTC: a máquina estava REALMENTE ocupada.** `%system` subiu
   de 2,5% para 52% e as **trocas de contexto pularam de 4.200/s para
   18.500/s** — com criação de processos inalterada (33/s). Não é fork bomb:
   é algo em laço apertado de syscalls. **O reboot matou o culpado e não deu
   para identificá-lo** — os contadores do boot anterior morreram com ele.
2. **22:10 UTC em diante: a máquina está CAPADA.** Usamos 5,8% e o provedor
   leva 85,3%. Isso não é consequência de carga nossa — é a limitação da
   Hostinger ainda ativa (ela reaplicou `ct_set_limits` às 21:01, depois de o
   João ter removido às 20:19).

## O que descartei com evidência

- **Processo em loop agora:** o maior consumidor acumulado é o `dockerd` com
  27 min de CPU em 1h49 de uptime — e ele passou o período fazendo build.
- **Disco cheio:** 116 GB de 193 GB (60%).
- **Memória:** 11,8 GB em cache, zero swap.
- **I/O:** `wa` em 0-1%, `b` (bloqueados) em 0.
- **Sync da Shopee/Facebook como causa:** o volume por hora é idêntico antes e
  depois do degrau (~95-100 runs/h). O run `interrupted` de 2h começou às
  14:02 BRT, **depois** da tempestade — é consequência.

## O que mudou no produto por causa disso

O painel passa a medir **CPU real e steal do host de dentro do container**
(`/proc/stat` não é isolado pelo Docker). É o sinal que a API da Hostinger não
dá: ela mostra "CPU 100%" tanto para "ocupada" quanto para "faminta", e os dois
pedem ações opostas. Alarme vermelho acima de 20% de steal; carga por vCPU
junto, porque load alto **com CPU ociosa** é fila, não trabalho.

⚠️ **Commitado na develop, NÃO subiu**: com steal em 85% um build leva o dobro
do tempo e já falhou uma vez hoje. Sobe quando a limitação sair.

## Para o chamado na Hostinger

O argumento está pronto e é difícil de contestar: **às 22:10 UTC a VPS
consumia 5,8% de CPU e sofreu 85,3% de steal**. Não é a máquina abusando —
é o teto binding com ela parada.
