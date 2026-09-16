# CI/CD — o Actions constrói, o VPS só puxa

**Desde 16/09/2026.** Antes, o Coolify rodava `docker build` **dentro do VPS que
serve produção**. Isso derrubou produção duas vezes em cinco dias — 11/09 (~20 h
fora do ar) e 16/09 (07:40-08:20 BRT) — sempre pelo mesmo mecanismo: o build
satura a CPU, a Hostinger aplica um teto de 20% na VPS inteira, e o teto é
auto-sustentável (com a fatia reduzida, a carga rotineira já satura o que
sobrou).

Agora nenhum `docker build` roda no VPS.

```
push na develop ─► validate ─► build (runner) ─► GHCR ─► deploy: Coolify PUXA ─► prova
push na main    ─► validate ─► build (runner) ─► GHCR ─► [APROVAÇÃO] ─► deploy ─► prova
```

## Workflows

| arquivo | dispara em | gate |
|---|---|---|
| `deploy-homologation.yml` | push na `develop`, ou dispatch | não |
| `deploy-production.yml` | push na `main`, ou dispatch | **sim** — `environment: production`, revisor `joaoivson` |
| `monitor-producao.yml` | cron | — sonda externa, avisa no WhatsApp |
| `limpar-ghcr.yml` | cron semanal | — mantém as 15 últimas versões |

Os dois de deploy têm `paths-ignore` para `**.md`: commit só de documentação não
deploya.

## Imagens

`ghcr.io/joaoivson/marketdash-backend` (target `api`) e `…-backend-worker`
(target `worker`), **públicas** — o VPS puxa sem `docker login`.

Tag imutável = **SHA completo** do commit; `main`/`develop` são tags móveis, de
conveniência. É a tag do SHA que identifica a versão.

## Scripts

| script | o que faz |
|---|---|
| `deploy-imagem.sh <alvo> <url> <imagem> <tag>` | trava `build_pack` → PATCH da tag → dispara → **poll até `finished`** → confere tag e status |
| `confirmar-versao.sh <url-health> <sha>` | poll do `/health` até `.version == sha` |
| ~~`aguardar-build.sh`~~, ~~`trigger-deploy.sh`~~ | mortos: serializavam builds no VPS. Nenhum workflow chama. Ficam até produção migrar (o `git revert` do pipeline precisaria deles) |

## As três travas

1. **`build_pack` tem de ser `dockerimage`.** `deploy-imagem.sh` **recusa
   disparar** numa app ainda em `dockerfile` — um POST nesse estado mandaria o
   VPS compilar. É a trava que importa durante a migração.
2. **Auto Deploy do Coolify DESLIGADO** em todas as apps. Ligado, o webhook do
   GitHub App dispara build no VPS pelas costas do CI.
3. **Gate de aprovação** no deploy de produção.

## CI verde agora significa deployado

Isto **inverte** o aviso antigo. O job só sai 0 depois do deployment chegar a
`finished`, da tag gravada bater, e do código novo responder na URL real:

```bash
curl -s https://api.marketdash.com.br/health | jq -r .version   # == o SHA empurrado
```

O aviso "CI verde ≠ deployado" continua valendo para app **não migrada** — mas
nessas o script recusa disparar e o job fica vermelho, que é o certo.

## Rollback — segundos, sem build

```bash
gh workflow run deploy-production.yml -f tag=<sha-anterior>
```

O input `tag` **pula** o job `build`: reaponta a imagem já publicada e redeploya.

## Quando o `build` fica vermelho com `denied`

É permissão de `packages: write` ou o pacote ficou privado. Confira se a imagem
continua pública pelo caminho que o VPS usa:

```bash
tok=$(curl -s "https://ghcr.io/token?scope=repository:joaoivson/marketdash-backend:pull&service=ghcr.io" | jq -r .token)
curl -s -H "Authorization: Bearer $tok" https://ghcr.io/v2/joaoivson/marketdash-backend/tags/list
```

Sem token de usuário: se isso responde, o VPS consegue puxar.

## Secrets e variáveis

`COOLIFY_API_TOKEN` (token **raiz** — o de leitura não faz PATCH),
`COOLIFY_DEPLOY_URL_*` (base e uuid saem da própria URL), `ALERTA_API_BASE` e
`CRON_SECRET` para a sonda.
