-- Migration 035: Painel administrativo MarketDash
-- Tabelas de histórico Kiwify, logins, despesas, notas, page_views, sync errors.
-- Acesso: is_admin = true apenas para relacionamento@marketdash.com.br

-- ---------------------------------------------------------------------------
-- subscription_events (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_events (
  id BIGSERIAL PRIMARY KEY,
  event_type TEXT NOT NULL,
  order_id TEXT,
  order_ref TEXT,
  order_status TEXT,
  subscription_id TEXT,
  customer_email TEXT,
  customer_name TEXT,
  customer_cpf TEXT,
  customer_phone TEXT,
  plan_id TEXT,
  plan_name TEXT,
  plan_frequency TEXT,
  amount_gross_cents INTEGER,
  fee_cents INTEGER,
  amount_net_cents INTEGER,
  payment_method TEXT,
  subscription_status TEXT,
  has_access BOOLEAN,
  access_until TIMESTAMPTZ,
  next_payment TIMESTAMPTZ,
  subscription_start TIMESTAMPTZ,
  approved_date TIMESTAMPTZ,
  refunded_at TIMESTAMPTZ,
  funds_status TEXT,
  deposit_date TIMESTAMPTZ,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  is_plan_change BOOLEAN NOT NULL DEFAULT false,
  raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  dedupe_key TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_events_dedupe
  ON subscription_events (dedupe_key);

CREATE INDEX IF NOT EXISTS ix_subscription_events_received_at
  ON subscription_events (received_at DESC);

CREATE INDEX IF NOT EXISTS ix_subscription_events_email
  ON subscription_events (lower(customer_email));

CREATE INDEX IF NOT EXISTS ix_subscription_events_cpf
  ON subscription_events (customer_cpf);

CREATE INDEX IF NOT EXISTS ix_subscription_events_user_id
  ON subscription_events (user_id);

CREATE INDEX IF NOT EXISTS ix_subscription_events_subscription_id
  ON subscription_events (subscription_id);

-- ---------------------------------------------------------------------------
-- user_logins
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS user_logins (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  logged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ip TEXT,
  user_agent TEXT
);

CREATE INDEX IF NOT EXISTS ix_user_logins_user_logged
  ON user_logins (user_id, logged_at DESC);

-- ---------------------------------------------------------------------------
-- expenses
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expenses (
  id BIGSERIAL PRIMARY KEY,
  date DATE NOT NULL,
  category TEXT NOT NULL,
  supplier TEXT,
  description TEXT,
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0),
  recurring BOOLEAN NOT NULL DEFAULT false,
  notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS ix_expenses_date ON expenses (date DESC);
CREATE INDEX IF NOT EXISTS ix_expenses_category ON expenses (category);

-- ---------------------------------------------------------------------------
-- admin_client_notes
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS admin_client_notes (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  author_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  body TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_admin_client_notes_user
  ON admin_client_notes (user_id, created_at DESC);

-- ---------------------------------------------------------------------------
-- page_views (ranking de telas)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS page_views (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  path TEXT NOT NULL,
  viewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_page_views_viewed_at ON page_views (viewed_at DESC);
CREATE INDEX IF NOT EXISTS ix_page_views_path ON page_views (path);

-- ---------------------------------------------------------------------------
-- sync_error_logs
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sync_error_logs (
  id BIGSERIAL PRIMARY KEY,
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  source TEXT NOT NULL, -- shopee | facebook
  error_message TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_sync_error_logs_created
  ON sync_error_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS ix_sync_error_logs_source
  ON sync_error_logs (source, created_at DESC);

-- ---------------------------------------------------------------------------
-- Seed admin
-- ---------------------------------------------------------------------------
UPDATE users
SET is_admin = true
WHERE lower(email) = 'relacionamento@marketdash.com.br';
