# STATUS — Build sai do VPS: Actions constrói, GHCR guarda, Coolify puxa

**Pedido do João (16/09/2026):** "essas quedas não podem acontecer; hoje produção
ficou fora das 8h às 10h — isso é inadmissível."

**Causa estrutural:** o Coolify faz `docker build` DENTRO do VPS que serve
produção. Derrubou produção 2× em 5 dias (11/09: ~20h; 16/09 07:40-08:20 BRT:
load 32, steal 94%). Plano completo em `~/.claude/plans/`.

**Decisões do João:** aprovação manual para deploy de produção (GitHub
Environment); produção só migra depois que a Hostinger tirar o teto;
`CELERY_CONCURRENCY=2` no worker de prod; apagar o Redis nº 2; enxugar a imagem.

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Mapear pipeline atual (2 repos) | dois `Explore` em paralelo | Explore: backend / frontend | — | ✅ | — |
| 2 | Desenhar a migração | `Plan`: crítica + passos + rollback | Plan agent | — | — | — |
| 3 | Backend: Dockerfile multi-stage | targets `worker` e `api`, `api` por último | eu | ✅ imagem 1,37→1,13 GB | ✅ os 2 targets constroem | — |
| 4 | Backend: `version` no /health e no boot | `APP_VERSION` + `/`, `/health`, worker log | eu | ✅ 1303 testes | ✅ container real devolveu `"version":"teste-local"` | — |
| 5 | Backend: `deploy-imagem.sh` + `confirmar-versao.sh` | trava `build_pack`, PATCH tag, poll até `finished` | eu | ✅ `bash -n` ok | ⬜ exercitado na etapa 13 | — |
| 6 | Backend: workflows prod/hml | build+push GHCR, gate de aprovação em prod | eu | ✅ YAML válido, `environment: production` | ⬜ | — |
| 7 | Frontend: Dockerfile com ARGs VITE_* | guarda contra tela branca + `version.json` | eu | ✅ imagem 98 MB | ✅ guarda recusa build sem chave; bundle com Supabase de hml; SPA 200 | — |
| 8 | Frontend: workflows | Variables por ambiente, hash exato do bundle | eu | ✅ tsc 25 (baseline), YAML ok | ⬜ | — |
| 9 | Retenção no GHCR | workflow semanal, mantém 15 versões | eu | ✅ nos 2 repos | — | — |
| 10 | Pré-verificações no Coolify | Auto Deploy OFF, ports_mappings, healthcheck | eu | — | ⬜ | — |
| 11 | Push em `develop` (etapa 1 do rollout) | imagens publicadas; deploy barrado pela trava | eu | ⬜ | ⬜ | — |
| 12 | Migrar as 5 apps de hml para Docker Image | PATCH ou B2 (SQL) com pg_dump antes | eu | ⬜ | ⬜ | — |
| 13 | Deploy de hml pela imagem + validação | `version == sha`, login, bundle, carga do host | eu | ⬜ | ⬜ | ⬜ |
| 14 | Promover para `main` (cherry-pick) | 131/73 commits da develop NÃO vão junto | eu | ⬜ | ⬜ | — |
| 15 | Migrar prod + deploy aprovado | **só com o teto da Hostinger removido** | João aprova | ⬜ | ⬜ | ⬜ |
| 16 | Docs, CHANGELOG, memória, limpeza | scripts antigos, Dockerfile.worker, Redis nº 2 | eu | ⬜ | — | — |

**Bloqueio da linha 15:** benchmark de CPU no host precisa voltar a < 1,8 s
(agora: 3,3-4,9 s). Etapas 1-14 não dependem disso.

## Ações que dependem do João
- GitHub Environment `production` nos 2 repos (revisor obrigatório).
- GitHub Variables do frontend (`VITE_SUPABASE_*_PROD/_HML`, etc.).
- Subir o Coolify quando for a hora das etapas 12/15.
- Decidir sobre os repositórios estarem **públicos** (fora do escopo, mas
  `README-DEPLOY.md` tem segredo do Supabase em claro).

## Medições desta etapa (16/09, local)

- Imagem do backend: **1,37 GB → 1,13 GB**. Não chegou aos ~450 MB do plano
  porque polars (164 MB), pandas (79) e numpy (71) são 314 MB de dependência
  real — o que saiu foi o gcc. `postgresql-client` só aparece num COMENTÁRIO
  do código, mas são 5,7 MB e ficou.
- Imagem do frontend: **98 MB**.
- `/health` do container construído: `{"status":"healthy","version":"teste-local",…}`.
- A guarda do frontend **recusou** um build sem `VITE_SUPABASE_*`, com a
  mensagem da tela branca — testado de propósito.
- Bundle da imagem de teste traz `ytjpdvjuxtvxacredekk` (Supabase de hml), não
  o de produção: a separação por ambiente funciona.
