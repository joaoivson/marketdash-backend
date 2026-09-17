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
| 10 | Pré-verificações no Coolify | Auto Deploy OFF, ports_mappings, healthcheck | eu | — | ✅ **17/09** auto-deploy `f` nas 8 | — |
| 11 | Push em `develop` (etapa 1 do rollout) | imagens publicadas; deploy barrado pela trava | eu | ✅ | ✅ **FEITA 16/09 14:47/14:53** | — |
| 12 | Migrar as 5 apps de hml para Docker Image | PATCH ou B2 (SQL) com pg_dump antes | eu | ✅ | ✅ as 5 | — |
| 13 | Deploy de hml pela imagem + validação | `version == sha`, login, bundle, carga do host | eu | ✅ | ✅ version+worker+fila | ✅ bundle hml |
| 14 | Promover para `main` (cherry-pick) | 3 commits backend + 4 frontend; 149/83 NÃO foram | eu | ✅ **17/09** | ✅ | — |
| 15 | Migrar prod + deploy aprovado | SQL nas 3 apps + gate aprovado pelo João | João aprova | ✅ **17/09 01:5x** | ✅ `/health.version` + `/version.json` | ✅ bundle prod |
| 16a | **Docs + CHANGELOG** | runbook §10 + §5/§9 corrigidas, 2 `.github/README.md`, `README-DEPLOY.md`, CHANGELOG | eu | ✅ **16/09** | — | — |
| 16b | Limpeza | apagar `aguardar-build.sh`/`trigger-deploy.sh`/`Dockerfile.worker`, Redis nº 2 | eu | ⬜ só depois da 15 | — | — |
| 17 | Tetos de recurso em hml | cpus + shares 256 + `CELERY_CONCURRENCY=2` (prod também) | eu | ✅ | ✅ reconferido lendo de volta | — |

**Bloqueio da linha 15:** benchmark de CPU no host precisa voltar a < 1,8 s
(agora: 3,3-4,9 s). Etapas 1-14 não dependem disso.

## Etapa 11 — prova de que foi feita (16/09)

Push real na `develop` nos dois repos:

| repo | run | evento | validate | build | deploy |
|---|---|---|---|---|---|
| backend | 35110954264 (14:47) | `push` | ✅ | ✅ | ❌ barrado |
| frontend | 35111648001 (14:53) | `push` | ✅ | ✅ | ❌ barrado |

As três imagens estão no GHCR e são **públicas, puxáveis anonimamente** —
testado pelo caminho que o VPS usa (`ghcr.io/token?scope=…:pull` sem
credencial), não pela API do GitHub. Sem `docker login` no host.

Os dois runs seguintes (15:17 e 15:21) são `workflow_dispatch` **com tag**, com
`build: skipped` — é o caminho de ROLLBACK funcionando como projetado: reusa a
imagem já publicada em vez de reconstruir.

Pré-requisitos conferidos no mesmo dia: 9 Variables no frontend com os valores
certos (HML→`ytjpdvj…`, PROD→`iprdyorx…`, **não trocadas**), todos os secrets
`COOLIFY_DEPLOY_URL_*`, e o Environment `production` com revisor obrigatório
`joaoivson` **nos dois repos**.

## Cherry-pick da etapa 14 — o conjunto exato (levantado em 16/09)

A `develop` tem **149 commits** à frente da `main` no backend e **83** no
frontend. Promover por merge levaria tudo isso junto — e `create_all()` no boot
criaria em produção toda tabela de model novo, sem RLS. O conjunto do pipeline
é pequeno e isolado:

**Backend** (`git cherry-pick` nesta ordem, a partir de `origin/main`):
```
e7ae85a  feat(deploy): o build sai do VPS — Actions constrói, GHCR guarda, Coolify puxa
d80970f  fix(ci): erro de rede no deploy precisa dizer o que aconteceu
e1a409c  fix(ci): token sem permissão não pode parecer 'app em modo build'
```

**Frontend:**
```
e5edadd  feat(deploy): o build sai do VPS — Actions constrói, GHCR guarda, Coolify puxa
cd1aa5f  fix(ci): erro de rede no deploy precisa dizer o que aconteceu
3e61f37  fix(ci): token sem permissão não pode parecer 'app em modo build'
115a4ab  chore(ci): baseline de tipos por branch (develop 25, main 26)
```

⚠️ `115a4ab` **não é opcional**: a `main` tem 26 erros de tipo pré-existentes e
a `develop` 25. Sem ele, o job `validate` da main reprova com "o número
aumentou" num commit que não mexeu em tipo nenhum.

Os scripts de infra (`limites-coolify.sh`, `teto_hostinger.py`) e o fix da
thumbnail do Instagram **não entram** neste cherry-pick — são rodadas próprias.

## ✅ PIPELINE NO AR EM PRODUÇÃO — 17/09/2026 ~01:55 UTC

Nenhum `docker build` roda mais no VPS. As 3 apps de produção puxam imagem do
GHCR.

| prova | valor |
|---|---|
| `api.marketdash.com.br/health` → `version` | `f7c5de3b8e8532e635c9717299d1aed952f353c0` |
| `marketdash.com.br/version.json` | `a81db3de740ac0c148c54d6662b05f1c56b23258` |
| bundle de produção | traz `iprdyorx…`, NÃO traz `ytjpdvj…` |
| latência (20 amostras) | mediana 0,104 s, máx 0,347 s, zero acima de 1 s |
| **queda durante a troca** | **nenhuma** — sonda a cada 5 s, zero eventos |

Benchmark do host: **0,99 s** (contra 4,70 s de mediana sob o teto).

### Dois achados que teriam mordido, pegos antes

1. **`scripts/worker-entrypoint.sh` não existia na `main`.** O target `worker` do
   Dockerfile novo faz `chmod +x` nele — o build de produção teria falhado. Foi
   trazido no cherry-pick; conferido que com `CELERY_PAPEL` ausente o
   comportamento é idêntico ao `CMD` antigo (a `main` já tinha `_fila_do_banco()`
   e `task_default_queue`).
2. **O entrypoint usa `CELERY_CONCURRENCY:-8`.** Produção subia sem `--concurrency`
   (default = nº de CPUs); o entrypoint a elevaria para 8 em silêncio. Por isso
   `CELERY_CONCURRENCY=2` virou PRÉ-REQUISITO do deploy, não melhoria.

## O benchmark de comparação — o comando EXATO

"Comparar com o mesmo benchmark" só vale se for literalmente o mesmo comando.
Este é o que produziu os números de 16/09 (laço Python puro, determinístico,
sem dependência de disco ou rede):

```bash
ssh root@31.97.22.173 'python3 -c "
import time
t=time.perf_counter()
x=0
for i in range(5000000): x+=i*i
print(f\"{time.perf_counter()-t:.2f}\")"'
```

| referência | valor |
|---|---|
| saudável | **1,0–1,5 s** |
| critério de "voltou ao normal" | **< 1,8 s** |
| **16/09 sob o teto** (100 execuções, 06:56–18:53 UTC) | mín **3,00 s** · mediana **4,70 s** · máx **17,00 s** |

🚫 **NÃO rodar antes das 22:00 UTC de 16/09.** Ele gera carga e contamina a
janela limpa acordada com o suporte. Depois da janela, é a medição que fecha o
diagnóstico. Exige SSH — é comando do João ou do suporte, não meu (o SSH daqui
está bloqueado pelo classificador).

## Estado real de produção sob o teto (medido, 16/09)

100 amostras do `/health` entre 06:56 e 18:53 UTC, a cada ~7 min:

| | |
|---|---|
| mediana | 0,58 s |
| acima de 1 s | **27%** |
| acima de 3 s | 11 |
| timeouts de 20 s | **3** (11:59, 12:21 e 18:32 UTC) |

Benchmark de CPU no host: mediana **4,70 s** contra 1,0-1,5 s saudável, pico 17 s.

⚠️ Spot check de 3 requisições dizia "0,16-0,43 s, saudável" — e estava errado.
Ver [[medicao-pontual-engana]].

⚠️ **Esse mesmo probe rodava benchmark no host a cada ~7 min, 100 vezes, entre
06:56 e 18:53 UTC.** Sobra da estratégia anterior; parado às **19:00 UTC**.
Declarado no chamado, porque se o critério de liberação automática olha PICO de
CPU, pode ter sido o que impedia. A janela limpa começa em 19:00 UTC de 16/09.

## Ordem operacional acordada (16/09, com o suporte da Hostinger)

Enquanto o teto estiver ativo: **nenhum build, nenhuma alteração em produção,
nenhum benchmark**. Carga artificial pode reiniciar o relógio das ~3 h.

1. **Remover o teto** — 🔄 **chamado ABERTO com a Hostinger em 16/09**, com
   quatro itens registrados: remover o limite de 20% da VPS 1239939; preservar
   produção, hml e Coolify sem alterações; rodar o scanner de malware só depois
   da remoção; revisar o consumo quando a CPU voltar ao normal. Evidência
   principal: o par controlado 12/09 (58,4%) → 13/09 (8,7%) e os 59,3% atuais
   com carga reduzida.

   ⚠️ **"Preservar sem alterações" = manter hml e Coolify PARADOS.** Foi eu que
   os parei em 16/09 para salvar produção; o estado a preservar é este, não o
   de antes. Subir qualquer um deles "para restaurar" recolocaria ~2,6 GB e a
   disputa de CPU de volta na máquina estrangulada.
2. **Medir de novo, com produção intacta** — `python3 scripts/teto_hostinger.py`.
   Passa quando a média diária volta à faixa do baseline (~8%) **e** o painel
   disser "CPU Limitations: No".
3. **Rodar o scan de malware** (instalar pelo hPanel). Depois da remoção, nunca
   antes: varredura de disco inteiro sob 20% de CPU demora horas e mantém a CPU
   cravada, podendo prolongar a limitação.
4. **Só então** investigar/ajustar Celery, Gunicorn e Coolify — inclusive a
   etapa 17 deste quadro.

Nota sobre a causa: com **92,5% de steal** (medido em 15/09 23:03 UTC),
Gunicorn/Celery/Traefik apareciam no `top` por estarem **famintos**, não por
consumirem. Steal mede CPU que o hypervisor reteve. A causa interna só é
mensurável com o teto fora.

## ⚠️ Não subir o Coolify enquanto o teto estiver ativo

Medido em 16/09: **com o Coolify de pé a demanda do VPS é 1,55 vCPU contra os
~0,8 que o teto de 20% libera**. Ou seja, subir o Coolify agora para aplicar a
etapa 17 produziria exatamente a lentidão que se quer evitar — o painel de
deploy competindo com produção pelo pouco de CPU que sobrou.

A etapa 17 não tem pressa: hml está **parada**, então o peso que ela teria já é
zero hoje. Os tetos existem para quando hml voltar.

**E produção parecer rápida não prova que o teto saiu.** Com a máquina ociosa,
um teto de 20% é invisível: ele só aparece quando se pede CPU. Foi assim que eu
errei uma vez, confiando em medição ociosa e disparando um deploy cedo demais.
A leitura confiável é o painel da Hostinger; benchmark no host é o plano B, e
tem de ser leve (1 núcleo, ~5 s) — carga pesada pode reiniciar o relógio das 3 h
que a Hostinger leva para soltar o teto depois que o uso normaliza.

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
