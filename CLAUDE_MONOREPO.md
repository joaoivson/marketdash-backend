# CLAUDE.md — MarketDash Monorepo

> **Onde este arquivo vive.** A raiz do monorepo **não é um repositório git** —
> um arquivo aqui não tem histórico, backup nem revisão. Desde 04/09/2026 o
> arquivo real mora em `marketdash-backend/CLAUDE_MONOREPO.md` e a raiz tem um
> symlink `CLAUDE.md` apontando para lá. Mesmo padrão do `CHANGELOG.md`. Edite
> pelo caminho que preferir — é o mesmo arquivo — e commite no repo do backend.

SaaS de analytics para marketing digital / afiliados. Dois subprojetos independentes com repos git separados.

## Estrutura

```
MarketDash/
├── marketdash-backend/   # FastAPI + PostgreSQL (Supabase) + Celery + Redis
├── marketdash-frontend/  # React + Vite + Zustand + shadcn/ui + Tailwind
└── .claude/              # Configuração Claude Code
```

Cada subprojeto tem seu próprio `CLAUDE.md` com detalhes de arquitetura, comandos e convenções. **Leia o CLAUDE.md do subprojeto antes de trabalhar nele.**

## Comandos Globais

```bash
# Backend
cd marketdash-backend && python -m uvicorn app.main:app --reload --port 8081
cd marketdash-backend && PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
cd marketdash-backend && celery -A app.tasks.celery_app worker --loglevel=info

# Frontend
cd marketdash-frontend && npm run dev    # Dev :8080, proxy /api → :8081
cd marketdash-frontend && npm run build
cd marketdash-frontend && npm run lint
cd marketdash-frontend && npx tsc -p tsconfig.app.json --noEmit

# Infra
cd marketdash-backend && docker-compose up  # PostgreSQL, Redis, App, Worker
```

> ⚠️ **Os dois comandos de verificação têm armadilha, e nos dois o erro é
> silencioso na direção errada:**
> - `pytest tests/ -v` **sempre falha** (o venv default é 3.9 e quebra na
>   coleção) — e a falha parece problema do código que se acabou de escrever.
>   Use o `.venv312` acima. Parte da suíte depende do Postgres local em
>   `:5434`; sem ele, esses arquivos **pulam em verde**.
> - `npx tsc --noEmit` na raiz do frontend **sempre passa**, mesmo com erro de
>   tipo: o `tsconfig.json` tem `"files": []`. Só `-p tsconfig.app.json` (ou
>   `npx tsc -b`) olha o `src/`. Há ~25 erros pré-existentes: o critério é
>   "não aumentou".

## Fluxo de Auth End-to-End

1. **Frontend**: Supabase Auth SDK → login → recebe JWT
2. **Frontend**: Envia `Authorization: Bearer <token>` em todas as requests via `api.config.ts`
3. **Backend**: `get_current_user()` valida token via `supabase.auth.get_user(token)` (NÃO decodifica JWT localmente)
4. **Backend**: Busca usuário local por email no PostgreSQL
5. **Backend**: `SET LOCAL app.current_user_id` para RLS
6. **Backend**: Retorna dados filtrados por `user_id`

## Pipeline de Dados

1. Usuário faz upload de CSV (comissões de afiliado)
2. Celery task processa CSV assincronamente → `DatasetRow` com `raw_data` JSONB
3. Dashboard calcula KPIs do `raw_data`:
   - Revenue: `raw_data["Valor de Compra(R$)"]`
   - Commission: `raw_data["Comissão líquida do afiliado(R$)"]`
   - **Profit = Commission - Ad Spend** (NÃO Revenue - Cost)
   - ROAS = Revenue / Ad Spend
4. Ad Spends são gerenciados separadamente em `/dashboard/investimentos`

## Regras Críticas

- **Isolamento de dados**: TODA query filtra por `user_id`. RLS via `SET LOCAL app.current_user_id`
- **Supabase**: SOMENTE para auth. Dados via SQLAlchemy
- **Camadas backend**: routes → services → repositories → models. Não pule camadas
- **Frontend state**: Components → Zustand stores → services → API. Components nunca chamam API diretamente
- **shadcn/ui**: Não modifique componentes em `components/ui/`. Estenda via wrappers

## Branches e deploy

```
develop → Actions constrói → GHCR → hml PUXA          (automático)
main    → Actions constrói → GHCR → [APROVAÇÃO] → prod PUXA
```

**Desde 17/09/2026 nenhum `docker build` roda no VPS.** O Coolify construía
dentro do servidor que serve produção e isso derrubou produção 2× em 5 dias.
Agora o GitHub Actions constrói, publica no GHCR, e o VPS só puxa a imagem
pronta. Detalhes em `docs/PROMOCAO_PARA_PRODUCAO.md` §10.

Os dois repos têm o mesmo par de workflows (`deploy-homologation.yml` /
`deploy-production.yml`), com `paths-ignore: '**.md'` — commit só de doc **não**
dispara deploy.

### As 8 apps do VPS e de onde vem a imagem de cada uma

| app | ambiente | imagem |
|---|---|---|
| API | produção | `ghcr.io/joaoivson/marketdash-backend:<sha>` |
| Worker Celery | produção | `ghcr.io/joaoivson/marketdash-backend-worker:<sha>` |
| Frontend | produção | `ghcr.io/joaoivson/marketdash-frontend:prod-<sha>` |
| API | hml | `…/marketdash-backend:<sha>` |
| Worker Celery | hml | `…/marketdash-backend-worker:<sha>` |
| Worker WhatsApp | hml | `…/marketdash-backend-worker:<sha>` (mesma imagem) |
| Frontend | hml | `…/marketdash-frontend:hml-<sha>` |
| WAHA | hml | `devlikeapro/waha:latest` (terceiro) |

Produção **não tem WAHA nem worker de WhatsApp** — o módulo de grupos é só hml.

⚠️ **Uma imagem por ambiente no frontend.** O Vite grava as `VITE_*` INLINE no
bundle; subir o artefato de hml em produção dá 401 em toda chamada autenticada.
Os valores vêm de **GitHub Variables** (`vars.VITE_*_PROD` / `_HML`), não mais
do painel do Coolify.

### Três travas que sustentam isso

1. `build_pack` tem de ser `dockerimage`. `deploy-imagem.sh` **recusa disparar**
   numa app em `dockerfile` — um POST nesse estado mandaria o VPS compilar.
2. **Auto Deploy do Coolify DESLIGADO** nas 8 apps. Ligado, o webhook do GitHub
   App compila no VPS pelas costas do CI. O campo **não vem pela API**: só por
   SQL em `application_settings`.
3. **Gate de aprovação** no deploy de produção (`environment: production`,
   revisor `joaoivson`).

**Existem dois caminhos para produção, e o default não é o merge.**

| Caminho | Quando | Como |
|---|---|---|
| **Cherry-pick em `main`** | correção isolada, sem migration, que não depende de feature não promovida — **é o caso comum** | `git worktree` a partir de `origin/main`, `git cherry-pick <sha>`, testar, `git push origin <branch>:main` |
| **Merge `develop → main`** | promoção de uma feature inteira, planejada | só com o runbook `marketdash-backend/docs/PROMOCAO_PARA_PRODUCAO.md` na mão |

**Por que o merge não é o default.** A `develop` acumula módulos ainda não
promovidos (em 04/09/2026: 83 commits no backend, 50 no frontend). "Sobe o fix X
pra produção" **não** autoriza levar esse acúmulo junto — e o risco não é
teórico: `Base.metadata.create_all()` roda no boot da API e **cria em produção
toda tabela de model novo, sem RLS**, antes de qualquer migration. Antes de
qualquer merge, liste o que mais vai (`git log --oneline origin/main..origin/develop`).

**CI verde AGORA é deploy feito** — isto inverteu em 17/09/2026. O job só sai 0
depois de o deployment chegar a `finished`, da tag gravada bater, e do código
novo **responder** na URL real:

```bash
curl -s https://api.marketdash.com.br/health | jq -r .version     # == SHA empurrado
curl -s https://marketdash.com.br/version.json | jq -r .version    # == SHA empurrado
```

O aviso antigo ("CI verde ≠ deploy") **continua valendo para app que ainda não
migrou** para Docker Image — mas nessas o script recusa disparar e o job fica
vermelho, que é o certo.

**Rollback são segundos**: `gh workflow run deploy-production.yml -f tag=<sha>`
pula o build e reaponta a imagem já publicada (as 15 últimas ficam no GHCR).

**Cherry-pick deixa rastro para depois:** o SHA em `main` é outro, então o merge
futuro da `develop` reconflita nesses arquivos. Registre o par de SHAs no
runbook e resolva mantendo o lado da develop.

## Convenções de Commit

```
feat: nova funcionalidade
fix: correção de bug
refactor: refatoração sem mudança de comportamento
docs: documentação
test: testes
chore: manutenção
```

Formato: `tipo(escopo): descrição curta` — ex: `feat(dashboard): adicionar filtro por período`

## Guia de Uso de Subagentes

Usar subagentes do Claude Code para tarefas complexas:

- **Explore agent**: Para investigar o codebase — buscar padrões, entender fluxos, mapear dependências
- **Plan agent**: Para planejar implementações — desenhar abordagem antes de codar
- **Agentes paralelos**: Lançar múltiplos agentes quando as tarefas são independentes (ex: explorar backend E frontend simultaneamente)
- **Worktree isolation**: Para mudanças experimentais sem afetar o working directory

## Troubleshooting Comum

| Problema | Causa | Solução |
|----------|-------|---------|
| CORS error no frontend | Backend não configurado para origin do dev | Verificar `CORS_ORIGINS` em `app/main.py` |
| 401 em requests | Token JWT expirado ou inválido | Verificar Supabase Auth, re-login |
| CSV não processa | Celery worker não rodando | `celery -A app.tasks.celery_app worker` |
| Dashboard sem dados | Dataset ainda processando | Verificar status do dataset na API |
| Proxy error no dev | Backend não rodando na porta 8081 | Iniciar uvicorn na porta correta |
