-- Migration: Criar tabela de cliques v2 (Agrupada e Otimizada)
-- Data: 2026-01-31

-- Criar nova tabela v2 sem apagar a antiga por segurança
CREATE TABLE click_rows_v2 (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    channel VARCHAR(255) NOT NULL,
    sub_id VARCHAR(255),
    clicks INTEGER NOT NULL DEFAULT 0,
    row_hash VARCHAR(32) UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Índices para performance
CREATE INDEX idx_click_rows_v2_user_date ON click_rows_v2(user_id, date);
CREATE INDEX idx_click_rows_v2_report ON click_rows_v2(user_id, date, channel);
