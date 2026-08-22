-- Migration 051: insights diários por PLACEMENT (publisher_platform)
--
-- Fase 1 da integração Instagram. A Marketing API já entrega o gasto/cliques de
-- cada campanha quebrado por plataforma de veiculação (`breakdowns=publisher_platform`):
-- facebook | instagram | messenger | audience_network | threads.
--
-- Sem esta tabela não dá pra responder "quanto do meu gasto foi pro Instagram e qual
-- o ROAS de lá" — `campaign_daily_insights` só guarda o total da campanha.
--
-- Uma linha por (campanha, dia, plataforma). O upsert é idempotente, igual ao de
-- campaign_daily_insights (migration 021).

CREATE TABLE IF NOT EXISTS campaign_platform_daily_insights (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    fb_campaign_id VARCHAR(64) NULL,
    date DATE NOT NULL,
    -- facebook | instagram | messenger | audience_network | threads | desconhecido
    publisher_platform VARCHAR(32) NOT NULL,
    spend DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    clicks INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0,
    cpc DOUBLE PRECISION NULL,
    ctr DOUBLE PRECISION NULL,
    -- reach NÃO é somável entre plataformas/dias (é deduplicado por período).
    -- Guardamos por transparência, mas nenhum KPI agregado soma esta coluna.
    reach INTEGER NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_platform_insight_campaign_date_platform
        UNIQUE(campaign_id, date, publisher_platform)
);

CREATE INDEX IF NOT EXISTS idx_platform_insight_user_date
    ON campaign_platform_daily_insights(user_id, date);
CREATE INDEX IF NOT EXISTS idx_platform_insight_user_platform_date
    ON campaign_platform_daily_insights(user_id, publisher_platform, date);
CREATE INDEX IF NOT EXISTS idx_platform_insight_campaign_id
    ON campaign_platform_daily_insights(campaign_id);

ALTER TABLE campaign_platform_daily_insights ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS campaign_platform_daily_insights_iso ON campaign_platform_daily_insights;
CREATE POLICY campaign_platform_daily_insights_iso ON campaign_platform_daily_insights
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);
