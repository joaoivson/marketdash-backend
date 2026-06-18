-- 029_backfill_order_status_pt.sql
-- O status do pedido vinha da API Shopee em inglês/maiúsculo (PENDING/COMPLETED/...)
-- e era gravado cru, então o dashboard exibia em inglês. A partir de agora a sync e o
-- parser de CSV normalizam para PT canônico (igual ao CSV: Concluído/Pendente/Cancelado).
-- Este backfill converte as linhas já existentes que ficaram em inglês.
--
-- Seguro quanto a row_hash: linhas com status em inglês são exclusivamente da API Shopee,
-- cujo row_hash NÃO inclui o status (é gerado por order_id+item_id+date+seq). Linhas de CSV
-- já estavam em PT e não são tocadas aqui.

UPDATE dataset_rows_v2 SET status = 'Pendente'  WHERE upper(status) = 'PENDING';
UPDATE dataset_rows_v2 SET status = 'Concluído' WHERE upper(status) = 'COMPLETED';
UPDATE dataset_rows_v2 SET status = 'Cancelado' WHERE upper(status) IN ('CANCELLED', 'CANCELED');
UPDATE dataset_rows_v2 SET status = 'Inválido'  WHERE upper(status) = 'INVALID';
UPDATE dataset_rows_v2 SET status = 'Rejeitado' WHERE upper(status) = 'REJECTED';

-- attribution_type (coluna criada na 025) nunca chegou a ser persistido pelo bulk_create,
-- então o histórico está NULL. Para popular: rodar um FULL REFRESH da Shopee e re-enviar os
-- CSVs (agora ambos gravam o tipo de atribuição). Não há o que backfillar via SQL aqui.
