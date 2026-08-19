---
name: "orquestrador-marketdash"
description: "Ponto de entrada do repositório backend do MarketDash: dada uma demanda, decide o que ler, qual skill/agent acionar e em que ordem, e garante o passo final obrigatório de atualizar a memória do time (CONTEXTO/DIARIO/DECISOES) e o CHANGELOG. Use no INÍCIO de qualquer sessão de trabalho neste repo, quando a demanda for ampla ou ambígua, quando o usuário perguntar 'por onde começo', 'o que uso pra isso', 'me ajuda com X', ou quando a tarefa cruzar mais de uma área (banco + service + rota + frontend). Use também ao FECHAR a tarefa, para não deixar contexto desatualizado."
---

# Orquestrador — MarketDash Backend

Este repo carrega skills próprias. **Não reinvente contexto: acione a skill
certa e siga o que ela diz.**

## 1. Antes de qualquer coisa

Leia `.claude/memoria/CONTEXTO.md` — é o estado atual do repo. Ele tem
precedência sobre qualquer suposição sua e sobre docs mais antigos
(inclusive sobre o `CLAUDE.md`, que já está desatualizado em pelo menos um
ponto: a porta do backend é **8000**, não 8081).

Depois, se a demanda não estiver clara em **qual usuário / qual integração /
qual número de negócio**, pergunte. Não assuma.

## 2. Roteamento por demanda

| A demanda é… | Leia antes | Acione |
|---|---|---|
| **Endpoint novo / alteração de rota** | `.claude/rules/camadas.md` | `/new-endpoint` → agent `api-fastapi` |
| **Migration, índice, schema** | `.claude/rules/isolamento-por-usuario.md` | agent `db-supabase` → `/new-migration` |
| **Query lenta / N+1** | — | agent `db-supabase` + skill `supabase-postgres-best-practices` |
| **Task, fila, sync que não roda** | `.claude/rules/celery-filas.md` | agent `celery-worker` |
| **Shopee / Facebook / Instagram / WhatsApp** | — | skill `integracoes-marketdash` |
| **Assinatura, plano, Kiwify, acesso** | — | skill `assinatura-kiwify-marketdash` |
| **MRR, churn, ARPU, DRE, aba Uso** | — | skill `admin-metricas-marketdash` → agent `admin-metrics-reviewer` |
| **KPI do dashboard (lucro, ROAS, canal)** | — | skill `marketdash-backend` §KPIs — e saiba que **o cálculo que a usuária vê é no frontend** |
| **Código Python: erro, log, tipo, teste** | `.claude/rules/code-style.md` | skill `python-fastapi-marketdash` |
| **Número errado por fuso / período** | `.claude/rules/fuso-e-datas.md` | skill `marketdash-backend` |
| **Bug / algo quebrado** | `CHANGELOG.md` da raiz | skill `systematic-debugging` → skill do domínio |
| **Feature nova, ideia ainda vaga** | — | skill `brainstorming` → `writing-plans` |
| **Revisar antes de mergear** | — | skill `requesting-code-review` |
| **Subir para produção** | — | `/deploy-check` |
| **Fim da tarefa** | — | **§4 desta skill** |

Múltiplos domínios na mesma tarefa: comece pelo que **bloqueia** os outros
(normalmente banco → repository → service → rota → frontend) e diga ao
usuário o que vem depois.

## 3. Regras que valem sempre

1. **Toda query filtra por `user_id`.** Buscar por id sem checar dono é
   vazamento; não-encontrado é **404**, não 403.
2. **Não pule camadas** — rota não faz `db.query`.
3. **Acesso é `subscription_has_access()`**, nunca `is_active` cru.
4. **Celery: priority 0 ou 9.** Nunca 5 — a task some em silêncio.
5. **Migration só ADICIONA**, e é idempotente (não há controle de versão).
6. **Bucketing por dia é BRT em Python** (`_brt_date()`), nunca
   `cast(coluna, Date)` em SQL.
7. **`Profit = Commission − Ad Spend`**. Nunca `Revenue − Cost`.
8. **`cost`/`profit` de `dataset_rows_v2` estão mortas** — não são fonte.
9. **Nunca use `user_id` como nome de query param novo.**
10. **Backend roda em Docker** (`docker-compose`), porta 8000.
11. **Nunca inventar** nome de tabela, coluna, endpoint, cliente ou processo.
    Não sabe? Leia o código ou pergunte.

## 4. Passo final — OBRIGATÓRIO, nunca pular

<HARD-GATE>
Nenhuma tarefa está concluída antes disto. Se você entregou código e não fez
os 4 passos abaixo, a tarefa está pela metade — o próximo dev (ou o próximo
chat) vai reconstruir contexto do zero e repetir erro já resolvido.
</HARD-GATE>

1. **Verificar** — rode e mostre a saída, não afirme sem evidência:
   ```bash
   PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
   docker-compose up -d && docker-compose ps
   ```
   Mexeu em algo que a usuária vê? **Valide na tela** (Playwright), em todos
   os pontos afetados — teste verde não prova tela certa.

2. **Atualizar a memória do time** (`.claude/memoria/`):
   - `CONTEXTO.md` — mudou o estado do repo? **Sobrescreva** a seção afetada.
   - `DIARIO.md` — **sempre**. Data, o que mudou, **por quê**, o que ficou
     pendente. Append, nunca reescrever entrada antiga.
   - `DECISOES.md` — tomou decisão técnica, achou débito ou deixou pendência?
     Registre com o motivo. Decisão sem motivo vira discussão repetida daqui
     a dois meses.

3. **Atualizar `CHANGELOG.md` da raiz** se a mudança é visível ao usuário —
   é o changelog único do monorepo, cobre backend e frontend juntos.

4. **Commit** com mensagem em português explicando **o porquê**, não só o quê.
   Formato `tipo(escopo): descrição`.

Ao final, diga ao usuário em uma linha o que foi atualizado — para ele poder
discordar.
