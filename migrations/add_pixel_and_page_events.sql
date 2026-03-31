-- Add Facebook Pixel ID to capture_sites
ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS facebook_pixel_id VARCHAR;

-- Create page_events table for internal tracking
CREATE TABLE IF NOT EXISTS page_events (
    id SERIAL PRIMARY KEY,
    site_id INTEGER NOT NULL REFERENCES capture_sites(id) ON DELETE CASCADE,
    event_type VARCHAR NOT NULL,
    utm_source VARCHAR,
    utm_medium VARCHAR,
    utm_campaign VARCHAR,
    utm_adset VARCHAR,
    utm_ad VARCHAR,
    referrer VARCHAR,
    user_agent VARCHAR,
    ip_address VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Index for dashboard queries (events by site + date range)
CREATE INDEX IF NOT EXISTS ix_page_events_site_created ON page_events (site_id, created_at);
