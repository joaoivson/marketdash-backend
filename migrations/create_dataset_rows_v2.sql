-- Migration: Criar tabela de vendas v2 (Agrupada e Otimizada)
-- Data: 2026-01-31

-- Criar nova tabela v2 sem apagar a antiga por segurança
CREATE TABLE dataset_rows_v2 (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Dimensões
    date DATE NOT NULL,
    platform VARCHAR(255),
    category VARCHAR(255),
    product VARCHAR(255) NOT NULL,
    status VARCHAR(255),
    sub_id1 VARCHAR(255),
    
    -- Métricas
    revenue NUMERIC(12, 2) DEFAULT 0,
    commission NUMERIC(12, 2) DEFAULT 0,
    cost NUMERIC(12, 2) DEFAULT 0,
    profit NUMERIC(12, 2) DEFAULT 0,
    quantity INTEGER DEFAULT 1,
    
    -- Identificador único para deduplicação
    row_hash VARCHAR(32) UNIQUE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Índices para performance
CREATE INDEX idx_dataset_rows_v2_user_date ON dataset_rows_v2(user_id, date);
CREATE INDEX idx_dataset_rows_v2_report ON dataset_rows_v2(user_id, date, platform, product);
