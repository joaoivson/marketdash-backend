-- Migration 014: Create shopee_integrations table
CREATE TABLE IF NOT EXISTS shopee_integrations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    app_id VARCHAR(255) NOT NULL,
    encrypted_password TEXT NOT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_sync_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_shopee_user UNIQUE(user_id)
);

CREATE INDEX IF NOT EXISTS idx_shopee_integrations_user_id ON shopee_integrations(user_id);

ALTER TABLE shopee_integrations ENABLE ROW LEVEL SECURITY;

CREATE POLICY shopee_integrations_iso ON shopee_integrations
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);
