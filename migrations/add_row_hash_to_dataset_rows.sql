-- Migration: Adicionar coluna row_hash para deduplicação de linhas
-- Data: 2026-01-30

ALTER TABLE dataset_rows 
ADD COLUMN IF NOT EXISTS row_hash VARCHAR(32);

-- Criar índice para busca rápida de duplicados
CREATE INDEX IF NOT EXISTS idx_dataset_rows_row_hash 
ON dataset_rows(row_hash);

-- Opcional: Criar índice composto com user_id para filtrar por usuário
CREATE INDEX IF NOT EXISTS idx_user_row_hash 
ON dataset_rows(user_id, row_hash);
