-- Migration: Simplificar e Agrupar vendas (datasets) por dia/plataforma/categoria/produto/status/subid
-- Data: 2026-01-31

-- 1. Remover tabela antiga para reconstruir com a nova estrutura simplificada
DROP TABLE IF EXISTS dataset_rows;

-- 2. Recriar tabela focada em agrupamento diário consolidado
CREATE TABLE dataset_rows (
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
    cost NUMERIC(12, 2) DEFAULT 0, -- Custo de anúncios aplicado
    profit NUMERIC(12, 2) DEFAULT 0,
    quantity INTEGER DEFAULT 1,
    
    -- Identificador único para deduplicação (Data + Plataforma + Categoria + Produto + Status + SubID)
    row_hash VARCHAR(32) UNIQUE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- 3. Índices para performance de relatórios e BI
CREATE INDEX idx_dataset_rows_user_date ON dataset_rows(user_id, date);
CREATE INDEX idx_dataset_rows_report ON dataset_rows(user_id, date, platform, product);
CREATE INDEX idx_dataset_rows_category ON dataset_rows(user_id, category);
CREATE INDEX idx_dataset_rows_sub_id ON dataset_rows(user_id, sub_id1);
CREATE INDEX idx_dataset_rows_hash ON dataset_rows(row_hash);
