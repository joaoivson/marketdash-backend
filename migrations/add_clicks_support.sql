-- Migration: Adicionar suporte para dados de Cliques (Clicks)
-- Data: 2026-01-30

-- 1. Atualizar tabela de datasets para suportar diferentes tipos
ALTER TABLE datasets 
ADD COLUMN IF NOT EXISTS type VARCHAR(32) DEFAULT 'transaction';

CREATE INDEX IF NOT EXISTS idx_datasets_type ON datasets(type);

-- 2. Criar tabela de linhas de cliques
CREATE TABLE IF NOT EXISTS click_rows (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    time TIME,
    channel VARCHAR(255) NOT NULL,
    sub_id VARCHAR(255),
    clicks INTEGER NOT NULL DEFAULT 0,
    row_hash VARCHAR(32),
    raw_data JSONB,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Criar índices analíticos para cliques
CREATE INDEX IF NOT EXISTS idx_click_rows_user_id ON click_rows(user_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_dataset_id ON click_rows(dataset_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_date ON click_rows(date);
CREATE INDEX IF NOT EXISTS idx_click_rows_channel ON click_rows(channel);
CREATE INDEX IF NOT EXISTS idx_click_rows_sub_id ON click_rows(sub_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_row_hash ON click_rows(row_hash);

-- Índices compostos para BI
CREATE INDEX IF NOT EXISTS idx_click_user_date ON click_rows(user_id, date);
CREATE INDEX IF NOT EXISTS idx_click_user_channel ON click_rows(user_id, channel);
CREATE INDEX IF NOT EXISTS idx_click_user_sub_id ON click_rows(user_id, sub_id);
CREATE INDEX IF NOT EXISTS idx_click_date_channel ON click_rows(date, channel);
