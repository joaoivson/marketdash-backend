-- Migration 021: Integração Facebook Ads + Campanhas
-- Cria facebook_integrations, campaigns e campaign_daily_insights.
-- Segue o padrão de RLS por app.current_user_id (ver migration 014/006).

-- ──────────────────────────────────────────────────────────────────────────
-- facebook_integrations: credenciais/token (long-lived, criptografado) por usuário
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS facebook_integrations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fb_user_id VARCHAR(64) NULL,
    fb_user_name VARCHAR(255) NULL,
    encrypted_access_token TEXT NOT NULL,
    token_expires_at TIMESTAMPTZ NULL,
    ad_account_id VARCHAR(64) NULL,
    ad_account_name VARCHAR(255) NULL,
    scopes TEXT NULL,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    last_sync_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_facebook_user UNIQUE(user_id)
);

CREATE INDEX IF NOT EXISTS idx_facebook_integrations_user_id ON facebook_integrations(user_id);

ALTER TABLE facebook_integrations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS facebook_integrations_iso ON facebook_integrations;
CREATE POLICY facebook_integrations_iso ON facebook_integrations
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- ──────────────────────────────────────────────────────────────────────────
-- campaigns: campanha sincronizada do Facebook, vinculável a um Sub ID Shopee
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaigns (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    fb_campaign_id VARCHAR(64) NOT NULL,
    ad_account_id VARCHAR(64) NULL,
    name VARCHAR(512) NOT NULL,
    objective VARCHAR(128) NULL,
    status VARCHAR(32) NULL,
    effective_status VARCHAR(64) NULL,
    daily_budget DOUBLE PRECISION NULL,
    lifetime_budget DOUBLE PRECISION NULL,
    sub_id VARCHAR(255) NULL,
    last_synced_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_campaign_user_fb UNIQUE(user_id, fb_campaign_id)
);

CREATE INDEX IF NOT EXISTS idx_campaign_user_subid ON campaigns(user_id, sub_id);
CREATE INDEX IF NOT EXISTS idx_campaigns_fb_campaign_id ON campaigns(fb_campaign_id);

ALTER TABLE campaigns ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS campaigns_iso ON campaigns;
CREATE POLICY campaigns_iso ON campaigns
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- ──────────────────────────────────────────────────────────────────────────
-- campaign_daily_insights: métricas diárias (Facebook Insights, level=campaign)
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS campaign_daily_insights (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    fb_campaign_id VARCHAR(64) NULL,
    date DATE NOT NULL,
    spend DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    clicks INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    cpc DOUBLE PRECISION NULL,
    ctr DOUBLE PRECISION NULL,
    reach INTEGER NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_insight_campaign_date UNIQUE(campaign_id, date)
);

CREATE INDEX IF NOT EXISTS idx_insight_user_date ON campaign_daily_insights(user_id, date);
CREATE INDEX IF NOT EXISTS idx_insight_campaign_id ON campaign_daily_insights(campaign_id);

ALTER TABLE campaign_daily_insights ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS campaign_daily_insights_iso ON campaign_daily_insights;
CREATE POLICY campaign_daily_insights_iso ON campaign_daily_insights
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);
