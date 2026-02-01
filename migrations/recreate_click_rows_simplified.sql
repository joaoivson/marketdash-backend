-- Migration: Simplificar e Agrupar cliques por dia/canal/subid
-- Data: 2026-01-31

-- 1. Remover tabela antiga para reconstruir com a nova estrutura ultra-simplificada
DROP TABLE IF EXISTS click_rows;

-- 2. Recriar tabela focada em agrupamento diário
CREATE TABLE click_rows (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    channel VARCHAR(255) NOT NULL,
    sub_id VARCHAR(255),
    clicks INTEGER NOT NULL DEFAULT 0,
    row_hash VARCHAR(32) UNIQUE, -- UNIQUE para evitar duplicatas por dia/canal/subid
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Índices para performance de relatórios
CREATE INDEX idx_click_rows_user_date ON click_rows(user_id, date);
CREATE INDEX idx_click_rows_report ON click_rows(user_id, date, channel);
CREATE INDEX idx_click_rows_sub_id ON click_rows(user_id, sub_id);
