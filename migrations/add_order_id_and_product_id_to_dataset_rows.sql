-- Migration: Adicionar order_id e product_id para melhor identificação de vendas
-- Data: 2026-02-03

-- Adicionar colunas na tabela v2 (ativa)
ALTER TABLE dataset_rows_v2
ADD COLUMN IF NOT EXISTS order_id VARCHAR,
ADD COLUMN IF NOT EXISTS product_id VARCHAR;

-- Adicionar índices para performance
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_order_id ON dataset_rows_v2(order_id);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_product_id ON dataset_rows_v2(product_id);

-- Adicionar colunas na tabela base (caso seja usada em algum fallback)
ALTER TABLE dataset_rows
ADD COLUMN IF NOT EXISTS order_id VARCHAR,
ADD COLUMN IF NOT EXISTS product_id VARCHAR;

CREATE INDEX IF NOT EXISTS idx_dataset_rows_order_id ON dataset_rows(order_id);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_product_id ON dataset_rows(product_id);
