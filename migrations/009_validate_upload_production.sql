-- Migration 009: Validação para Upload em Produção
-- Este script verifica e aplica todas as estruturas necessárias para o upload funcionar em produção.
-- Pode ser executado múltiplas vezes (idempotente). Use no Supabase SQL Editor do projeto de produção.
-- Data: 2026-02-11

-- =====================================================
-- 1. Tabelas jobs e job_chunks (Pipeline de Jobs)
-- =====================================================
CREATE TABLE IF NOT EXISTS jobs (
  job_id UUID PRIMARY KEY,
  dataset_id INT NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
  user_id INT NOT NULL REFERENCES users(id),
  type VARCHAR(32) NOT NULL DEFAULT 'transaction',
  storage_key TEXT NOT NULL,
  created_at TIMESTAMPTZ DEFAULT now(),
  status VARCHAR(20) DEFAULT 'queued',
  total_chunks INT DEFAULT 0,
  chunks_done INT DEFAULT 0,
  meta JSONB
);

CREATE TABLE IF NOT EXISTS job_chunks (
  job_id UUID REFERENCES jobs(job_id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  storage_key TEXT NOT NULL,
  status VARCHAR(20) DEFAULT 'queued',
  error TEXT,
  PRIMARY KEY (job_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_jobs_dataset_id ON jobs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at);

-- =====================================================
-- 2. dataset_rows_v2 - colunas necessárias
-- =====================================================
-- Garantir que dataset_rows_v2 existe (base mínima)
CREATE TABLE IF NOT EXISTS dataset_rows_v2 (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    platform VARCHAR(255),
    category VARCHAR(255),
    product VARCHAR(255) NOT NULL,
    status VARCHAR(255),
    sub_id1 VARCHAR(255),
    revenue NUMERIC(12, 2) DEFAULT 0,
    commission NUMERIC(12, 2) DEFAULT 0,
    cost NUMERIC(12, 2) DEFAULT 0,
    profit NUMERIC(12, 2) DEFAULT 0,
    quantity INTEGER DEFAULT 1,
    row_hash VARCHAR(32) UNIQUE
);

-- Colunas adicionais (order_id, product_id, time)
ALTER TABLE dataset_rows_v2 ADD COLUMN IF NOT EXISTS order_id VARCHAR;
ALTER TABLE dataset_rows_v2 ADD COLUMN IF NOT EXISTS product_id VARCHAR;
ALTER TABLE dataset_rows_v2 ADD COLUMN IF NOT EXISTS time TIME;

CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_order_id ON dataset_rows_v2(order_id);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_product_id ON dataset_rows_v2(product_id);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_user_date ON dataset_rows_v2(user_id, date);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_row_hash ON dataset_rows_v2(row_hash);

-- =====================================================
-- 3. click_rows_v2 - colunas necessárias
-- =====================================================
CREATE TABLE IF NOT EXISTS click_rows_v2 (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    channel VARCHAR NOT NULL,
    sub_id VARCHAR,
    clicks INTEGER NOT NULL DEFAULT 0,
    row_hash VARCHAR(32) UNIQUE
);

ALTER TABLE click_rows_v2 ADD COLUMN IF NOT EXISTS time TIME;

CREATE INDEX IF NOT EXISTS idx_click_rows_v2_dataset_id ON click_rows_v2(dataset_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_user_id ON click_rows_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_date ON click_rows_v2(date);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_row_hash ON click_rows_v2(row_hash);
CREATE INDEX IF NOT EXISTS idx_click_user_report ON click_rows_v2(user_id, date, channel);

-- =====================================================
-- 4. datasets - row_count e status
-- =====================================================
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS row_count INTEGER DEFAULT 0;
ALTER TABLE datasets ADD COLUMN IF NOT EXISTS status VARCHAR(20) DEFAULT 'pending';
CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets(status);

-- =====================================================
-- 5. RLS (Row Level Security) para dataset_rows_v2 e click_rows_v2
-- =====================================================
ALTER TABLE dataset_rows_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE click_rows_v2 ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rows_v2_iso ON dataset_rows_v2;
CREATE POLICY rows_v2_iso ON dataset_rows_v2
  FOR ALL USING (user_id::text = current_setting('app.current_user_id', true));

DROP POLICY IF EXISTS clicks_v2_iso ON click_rows_v2;
CREATE POLICY clicks_v2_iso ON click_rows_v2
  FOR ALL USING (user_id::text = current_setting('app.current_user_id', true));
