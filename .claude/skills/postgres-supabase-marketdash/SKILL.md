---
name: "postgres-supabase-marketdash"
description: "Banco do MarketDash: schema, migrations sem Alembic, índices, RLS, pg_cron, os dois projetos Supabase (produção e homologação) e performance de query. Use ao criar migration, investigar query lenta, mexer em model, ou responder 'este schema já está aplicado?'."
---

# PostgreSQL / Supabase — MarketDash

## Os dois ambientes

Produção e homologação são **projetos Supabase separados** — e não sempre
foram. Até 25/07 dividiam a mesma instância, e um cron de sync 24×/dia
derrubou os dois juntos. Ambiente separado é o que impede o incidente de
repetir; não volte a apontar hml para o banco de produção "só para testar".

O ref do projeto aparece no `DATABASE_URL` e é o que dá nome à fila do Celery
(`_fila_do_banco()`). Ver ref e credencial: no gerenciador de segredos / no
`.env` do ambiente — **nunca commitado, nunca colado em chat**.

## Migrations — o que o projeto não tem

- **Sem Alembic.** `migrations/` tem 75 arquivos `.sql` soltos.
- **Sem tabela de controle de versão.** Não existe `schema_migrations`
  populada: "esta migration já rodou aqui?" só se responde inspecionando o
  objeto (coluna, índice, constraint) no banco.
- Aplicação: `scripts/apply_migrations.py`, `psql`, ou a Management API do
  Supabase.

Consequências práticas, e são regras:

1. **Toda migration é idempotente** — `IF NOT EXISTS` em tudo. Reaplicar é
   normal e não pode quebrar.
2. **Toda migration é aditiva** — sem `DROP TABLE`/`DROP COLUMN`/`TRUNCATE`.
   Dado de comissão é financeiro; a perda é irreversível.
3. **Migration com `UPDATE` de dado real é perigosa por natureza** —
   reaplicar mexe em dado bom. Verifique se já rodou antes de rodar.
4. **Nunca "aplique todas"** num banco sem controle de versão. Verificação
   read-only custa minutos e responde a pergunta inteira.

## Índices

As consultas quentes filtram por usuário e período. O par
`(user_id, date)` é o índice que sustenta dashboard, export e agregação.
Coluna nova que entra em `WHERE` nasce com índice — em tabela de milhões de
linhas de comissão, sequential scan não aparece em dev e mata em produção.

Antes de otimizar: `EXPLAIN (ANALYZE, BUFFERS)`. Para padrões gerais de
Postgres, a skill `supabase-postgres-best-practices` complementa esta.

## RLS

`SET LOCAL app.current_user_id` é setado por `get_current_user()`. **É a
segunda linha de defesa** — o filtro por `user_id` na query continua
obrigatório. `SET LOCAL` morre com a transação: script, task e sessão nova
não herdam nada.

O **service client** do Supabase contorna RLS. Use só para Storage; nunca
para ler dado de usuário.

## pg_cron + pg_net

O agendamento vive **no banco**, não no Celery Beat. As migrations de cron
criam jobs que fazem `POST` em `/api/v1/internal/cron/*` com `CRON_SECRET`.

- Cadência: **1×/dia**.
- Mudou path ou payload do endpoint? O cron quebra **em silêncio** — a falha
  fica no log do cron, dentro do banco, não no log da API.
- Endpoint de cron novo entra em `internal.py` **e** ganha um job por
  migration.

## Tabelas centrais

| Tabela | Observação |
|---|---|
| `dataset_rows_v2` | A verdade está no `raw_data` JSONB. **`cost` e `profit` estão mortas** |
| `click_rows_v2` | Origem do canal real e da categoria |
| `ad_spends` | Gasto manual + espelho do Meta (com `source` e backup do manual) |
| `subscriptions` / `subscription_events` | Evento com `user_id` nulo existe (import histórico) |
| `sync_runs` / `sync_error_log` | Observabilidade de sync; alimenta `/admin/sincronizacoes` |

## Fuso

`cast(timestamptz, Date)` trunca no fuso da **sessão do Postgres**, não em
BRT. Não agrupe por dia civil em SQL — traga os `datetime` e faça o bucket em
Python com `_brt_date()`.
