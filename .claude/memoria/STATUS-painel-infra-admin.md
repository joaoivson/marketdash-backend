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
