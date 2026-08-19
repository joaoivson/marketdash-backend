---
description: Celery, filas, prioridade e tarefas agendadas no MarketDash
globs: "app/tasks/**/*.py"
---

# Celery — as duas armadilhas que já derrubaram o produto

## 1. Prioridade: **0 ou 9. Nunca outro valor.**

```python
minha_task.apply_async(args=[...], priority=0)   # interativo (usuário esperando)
minha_task.apply_async(args=[...], priority=9)   # batch (cron, full refresh)
```

O Redis não tem prioridade nativa — o Celery emula criando **uma fila por
step** (`[0, 3, 6, 9]`). Neste ambiente **só as pontas são consumidas**. Uma
task com `priority=5` (o default antigo do Celery) cai num step intermediário
que ninguém consome: é aceita, devolve 202, e **nunca executa**. Sem erro,
sem log, sem timeout. Foi exatamente isso que fez o sync manual da Shopee
"não fazer nada" por dias.

`task_default_priority=0` está setado, então `.delay()` sem prioridade é
seguro. O perigo é escrever `priority=5` achando que é "no meio".

## 2. Fila derivada do banco, não do ambiente

`_fila_do_banco()` monta `marketdash-<ref-do-projeto-supabase>` a partir do
`DATABASE_URL`. **Não troque isso por `ENVIRONMENT`** — os dois ambientes
reportam `"development"`, e produção e homologação dividem o mesmo Redis/0.

O que acontecia antes: a task de produção caía no worker de homologação, que
procurava o registro no banco dele, não achava e retornava em silêncio —

```python
dataset = repo.get_by_id(dataset_id, user_id)
if not dataset:
    return          # status fica "pending" pra sempre, sem erro
```

Era a causa de ~50% dos uploads travados e de a tabela `datasets` nunca ter
registrado um único `status='error'`.

## 3. Task nova precisa entrar no `include`

`celery_app.conf.include` lista os módulos explicitamente. `autodiscover`
está ligado, mas o `include` é o que garante registro no worker em produção —
sem ele aparece `unregistered task` só no ambiente real.

## 4. Não existe Celery Beat

O agendamento é **pg_cron + pg_net no Supabase**, chamando
`POST /api/v1/internal/cron/*` com `CRON_SECRET`. Agendamento novo se cria
**no banco** (migration) e um endpoint em `internal.py` — não com
`beat_schedule`.

Cadência: **1×/dia**. A versão de hora em hora derrubou o banco compartilhado.

## 5. Task falha em silêncio se não logar

Task não tem quem veja a exceção. Toda task:

- recebe `user_id` explícito e filtra por ele
- loga início e fim com o id do registro
- grava estado terminal (`error`) no banco em caso de falha — `return` mudo
  deixa o registro `pending` para sempre, e foi assim que o bug de fila ficou
  invisível por semanas
