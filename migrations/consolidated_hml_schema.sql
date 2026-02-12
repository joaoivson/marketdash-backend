-- consolidated_hml_schema.sql
-- Consolidação total do schema do MarketDash para ambiente de Homologação (HML)
-- Data: 2026-02-12

-- 1. Extensões
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- 2. Tabela: users
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    cpf_cnpj VARCHAR(255) UNIQUE,
    email VARCHAR(255) UNIQUE NOT NULL,
    hashed_password VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    password_set_token VARCHAR(255),
    password_set_token_expires_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_cpf_cnpj ON users(cpf_cnpj);
CREATE INDEX IF NOT EXISTS idx_users_password_set_token ON users(password_set_token);

-- 3. Tabela: datasets
CREATE TABLE IF NOT EXISTS datasets (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    filename VARCHAR(255) NOT NULL,
    type VARCHAR(32) DEFAULT 'transaction',
    status VARCHAR(20) DEFAULT 'pending',
    error_message TEXT,
    row_count INTEGER DEFAULT 0,
    uploaded_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_datasets_user_id ON datasets(user_id);
CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets(status);
CREATE INDEX IF NOT EXISTS idx_datasets_type ON datasets(type);
CREATE INDEX IF NOT EXISTS idx_dataset_user_uploaded ON datasets(user_id, uploaded_at);

-- 4. Tabela: dataset_rows_v2
CREATE TABLE IF NOT EXISTS dataset_rows_v2 (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    time TIME,
    platform VARCHAR(255),
    category VARCHAR(255),
    product VARCHAR(255) NOT NULL,
    status VARCHAR(255),
    sub_id1 VARCHAR(255),
    order_id VARCHAR(255),
    product_id VARCHAR(255),
    revenue NUMERIC(12, 4) DEFAULT 0,
    commission NUMERIC(12, 4) DEFAULT 0,
    cost NUMERIC(12, 4) DEFAULT 0,
    profit NUMERIC(12, 4) DEFAULT 0,
    quantity INTEGER DEFAULT 1,
    row_hash VARCHAR(32) UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_dataset_id ON dataset_rows_v2(dataset_id);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_user_id ON dataset_rows_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_date ON dataset_rows_v2(date);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_platform ON dataset_rows_v2(platform);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_v2_row_hash ON dataset_rows_v2(row_hash);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_user_report ON dataset_rows_v2(user_id, date, platform, product);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_user_category ON dataset_rows_v2(user_id, category);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_user_sub_id ON dataset_rows_v2(user_id, sub_id1);

-- 5. Tabela: click_rows_v2
CREATE TABLE IF NOT EXISTS click_rows_v2 (
    id SERIAL PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    time TIME,
    channel VARCHAR(255) NOT NULL,
    sub_id VARCHAR(255),
    clicks INTEGER DEFAULT 0,
    row_hash VARCHAR(32) UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_click_rows_v2_dataset_id ON click_rows_v2(dataset_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_user_id ON click_rows_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_date ON click_rows_v2(date);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_channel ON click_rows_v2(channel);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_row_hash ON click_rows_v2(row_hash);
CREATE INDEX IF NOT EXISTS idx_click_user_report ON click_rows_v2(user_id, date, channel);

-- 6. Tabela: subscriptions
CREATE TABLE IF NOT EXISTS subscriptions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    plan VARCHAR(64) DEFAULT 'free',
    is_active BOOLEAN DEFAULT FALSE,
    last_validation_at TIMESTAMP WITH TIME ZONE,
    cakto_customer_id VARCHAR(255),
    expires_at TIMESTAMP WITH TIME ZONE,
    cakto_transaction_id VARCHAR(255),
    cakto_status VARCHAR(64),
    cakto_offer_name VARCHAR(255),
    cakto_due_date TIMESTAMP WITH TIME ZONE,
    cakto_subscription_status VARCHAR(64),
    cakto_payment_status VARCHAR(64),
    cakto_payment_method VARCHAR(64),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_user_id ON subscriptions(user_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_customer_id ON subscriptions(cakto_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_last_validation ON subscriptions(last_validation_at);

-- 7. Tabela: ad_spends
CREATE TABLE IF NOT EXISTS ad_spends (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    date DATE NOT NULL,
    sub_id VARCHAR(255),
    amount FLOAT NOT NULL,
    clicks INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_ad_spends_user_id ON ad_spends(user_id);
CREATE INDEX IF NOT EXISTS idx_ad_spends_date ON ad_spends(date);
CREATE INDEX IF NOT EXISTS idx_ad_spends_sub_id ON ad_spends(sub_id);
CREATE INDEX IF NOT EXISTS idx_ad_spend_user_date ON ad_spends(user_id, date);
CREATE INDEX IF NOT EXISTS idx_ad_spend_user_sub_date ON ad_spends(user_id, sub_id, date);
CREATE INDEX IF NOT EXISTS idx_ad_spend_user_date_id ON ad_spends(user_id, date, id);

-- 8. Tabela: jobs e job_chunks
CREATE TABLE IF NOT EXISTS jobs (
    job_id UUID PRIMARY KEY,
    dataset_id INTEGER NOT NULL REFERENCES datasets(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id),
    type VARCHAR(32) DEFAULT 'transaction',
    storage_key TEXT NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    status VARCHAR(20) DEFAULT 'queued',
    total_chunks INTEGER DEFAULT 0,
    chunks_done INTEGER DEFAULT 0,
    meta JSONB
);

CREATE INDEX IF NOT EXISTS idx_jobs_dataset_id ON jobs(dataset_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_id ON jobs(user_id);
CREATE INDEX IF NOT EXISTS idx_jobs_user_created ON jobs(user_id, created_at);

CREATE TABLE IF NOT EXISTS job_chunks (
    job_id UUID NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    storage_key TEXT NOT NULL,
    status VARCHAR(20) DEFAULT 'queued',
    error TEXT,
    PRIMARY KEY (job_id, chunk_index)
);

-- 9. Row Level Security (RLS)
ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE click_rows_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE ad_spends ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions ENABLE ROW LEVEL SECURITY;

-- Políticas baseadas na variável de sessão 'app.current_user_id'
-- O Backend utiliza: SET LOCAL app.current_user_id = '123';

DROP POLICY IF EXISTS datasets_iso ON datasets;
CREATE POLICY datasets_iso ON datasets 
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

DROP POLICY IF EXISTS rows_v2_iso ON dataset_rows_v2;
CREATE POLICY rows_v2_iso ON dataset_rows_v2 
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

DROP POLICY IF EXISTS clicks_v2_iso ON click_rows_v2;
CREATE POLICY clicks_v2_iso ON click_rows_v2 
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

DROP POLICY IF EXISTS ad_spends_iso ON ad_spends;
CREATE POLICY ad_spends_iso ON ad_spends 
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

DROP POLICY IF EXISTS subscriptions_iso ON subscriptions;
CREATE POLICY subscriptions_iso ON subscriptions 
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- Mensagem final
DO $$
BEGIN
    RAISE NOTICE '✅ Schema consolidado aplicado com sucesso no HML!';
    RAISE NOTICE '🔒 RLS habilitado em: datasets, dataset_rows_v2, click_rows_v2, ad_spends, subscriptions';
END $$;
