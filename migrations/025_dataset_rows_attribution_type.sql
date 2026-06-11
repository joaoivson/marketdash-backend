-- 025_dataset_rows_attribution_type.sql
-- Captura o attributionType da Shopee (nível de item do conversionReport) para
-- distinguir venda DIRETA (ORDERED_IN_SAME_SHOP) de cookie/cross-shop
-- (ORDERED_IN_DIFFERENT_SHOP). Antes era descartado e o card "Diretos" dava 0·0%.
--
-- Após aplicar, rodar um FULL REFRESH da Shopee para popular o histórico
-- (linhas antigas ficam com attribution_type NULL = contam como não-direto).

ALTER TABLE dataset_rows_v2
    ADD COLUMN IF NOT EXISTS attribution_type VARCHAR;

CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_attribution_type
    ON dataset_rows_v2 (attribution_type);
