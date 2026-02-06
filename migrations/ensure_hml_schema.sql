-- Consolidação de migrations recentes para garantir consistência no HML
-- Este script é seguro para rodar múltiplas vezes (Idempotente)

-- 1. Datasets (status e row_count)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'datasets' AND column_name = 'row_count') THEN
        ALTER TABLE datasets ADD COLUMN row_count INTEGER DEFAULT 0;
    END IF;
    
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'datasets' AND column_name = 'status') THEN
        ALTER TABLE datasets ADD COLUMN status VARCHAR(20) DEFAULT 'pending';
        CREATE INDEX IF NOT EXISTS idx_datasets_status ON datasets(status);
        -- Atualizar status null/pending antigos para completed
        UPDATE datasets SET status = 'completed' WHERE status IS NULL OR status = 'pending';
    END IF;
END $$;

-- 2. Users (password_set_token)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'password_set_token') THEN
        ALTER TABLE users ADD COLUMN password_set_token VARCHAR(255);
        CREATE INDEX IF NOT EXISTS idx_users_password_set_token ON users(password_set_token);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'users' AND column_name = 'password_set_token_expires_at') THEN
        ALTER TABLE users ADD COLUMN password_set_token_expires_at TIMESTAMP WITH TIME ZONE;
    END IF;
END $$;

-- 3. Dataset Rows (row_hash)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'dataset_rows' AND column_name = 'row_hash') THEN
        ALTER TABLE dataset_rows ADD COLUMN row_hash VARCHAR(32);
        CREATE INDEX IF NOT EXISTS idx_dataset_rows_row_hash ON dataset_rows(row_hash);
        CREATE INDEX IF NOT EXISTS idx_user_row_hash ON dataset_rows(user_id, row_hash);
    END IF;
END $$;

-- 4. Subscriptions (Cakto integration fields)
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name = 'subscriptions' AND column_name = 'last_validation_at') THEN
        ALTER TABLE subscriptions 
        ADD COLUMN last_validation_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN cakto_customer_id VARCHAR(255),
        ADD COLUMN expires_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN cakto_transaction_id VARCHAR(255),
        ADD COLUMN cakto_status VARCHAR(64),
        ADD COLUMN cakto_offer_name VARCHAR(255),
        ADD COLUMN cakto_due_date TIMESTAMP WITH TIME ZONE,
        ADD COLUMN cakto_subscription_status VARCHAR(64),
        ADD COLUMN cakto_payment_status VARCHAR(64),
        ADD COLUMN cakto_payment_method VARCHAR(64);

        CREATE INDEX IF NOT EXISTS idx_subscriptions_last_validation ON subscriptions(last_validation_at);
        CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_customer_id ON subscriptions(cakto_customer_id);
    END IF;
END $$;

-- 5. Click Rows V2 (Verificar se tabela existe)
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

CREATE INDEX IF NOT EXISTS idx_click_rows_v2_dataset_id ON click_rows_v2(dataset_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_user_id ON click_rows_v2(user_id);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_date ON click_rows_v2(date);
CREATE INDEX IF NOT EXISTS idx_click_rows_v2_row_hash ON click_rows_v2(row_hash);
CREATE INDEX IF NOT EXISTS idx_click_user_report ON click_rows_v2(user_id, date, channel);
