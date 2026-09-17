-- 084 — Modal "Vincular Sub ID": índice de cobertura + visibility map
--
-- ## O sintoma
--
-- O modal de `/dashboard/campanhas` levava ~10 s para abrir em produção. A
-- consulta é `sub_id_sales_summary` (campaign_repository.py): agrega pedidos e
-- comissão por sub_id nos últimos 30 dias.
--
-- ## A medição (17/09/2026)
--
-- Produção, EXPLAIN do plano: `Bitmap Heap Scan` lendo **5.709 blocos** (~45 MB)
-- para produzir 130 linhas. O índice `(user_id, date)` acha as linhas em 112 ms;
-- os segundos restantes são a leitura do heap, porque as colunas agregadas
-- (`sub_id1`, `status`, `commission`, `order_id`) não estão no índice.
--
-- ## Por que as DUAS coisas, e não só o índice
--
-- Medido em homologação, com 210 mil linhas:
--
--   sem índice ................ 1.219 buffers, 83 ms
--   índice, SEM vacuum ........ 1.096 buffers, 10.503 heap fetches, 150 ms  ← PIOR
--   índice + VACUUM ........... 124 buffers, 0 heap fetches, 34 ms
--
-- O índice sozinho **piora**: o plano vira `Index Only Scan`, mas sem o
-- visibility map atualizado o Postgres visita o heap de toda linha do mesmo
-- jeito — e agora com um índice maior para varrer. É o `VACUUM` que marca as
-- páginas como all-visible e torna o "index only" verdadeiro.
--
-- ## Manutenção
--
-- `dataset_rows_v2` recebe upsert do sync da Shopee, então o visibility map
-- envelhece rápido. O autovacuum padrão do Postgres só dispara com 20% da
-- tabela morta — em 324 mil linhas, 65 mil tuplas. Baixamos o gatilho para 5%
-- NESTA tabela, para o index-only scan continuar valendo.

-- Fora de transação, para não bloquear escrita. Se o arquivo for aplicado por
-- uma ferramenta que abre transação, rode estas linhas manualmente.
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dsr_v2_subid_modal
  ON dataset_rows_v2 (user_id, date)
  INCLUDE (sub_id1, status, commission, order_id);

VACUUM (ANALYZE) dataset_rows_v2;

ALTER TABLE dataset_rows_v2 SET (
  autovacuum_vacuum_scale_factor = 0.05,
  autovacuum_analyze_scale_factor = 0.05
);

-- Conferência: `Heap Fetches: 0` e `Index Only Scan` no plano.
--   EXPLAIN (ANALYZE, BUFFERS) SELECT ... (ver sub_id_sales_summary)
