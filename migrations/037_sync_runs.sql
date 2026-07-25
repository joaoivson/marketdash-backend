-- Migration 037: sync_runs — log de execução de sincronizações (Shopee + Facebook)
--
-- Hoje só existe sync_error_logs (só falhas, sem duração/contagem) e last_sync_at
-- (timestamp isolado, sobrescrito, sem histórico). sync_runs registra CADA execução
-- (running -> success/failed/skipped_lock), com duração calculável e contagem de
-- registros, para responder "a madrugada completou pra todo mundo?" via query.
--
-- Aditiva: não substitui sync_error_logs, que continua sendo escrito como hoje.

CREATE TABLE IF NOT EXISTS sync_runs (
  id BIGSERIAL PRIMARY KEY,
  source TEXT NOT NULL,                       -- 'shopee' | 'facebook'
  trigger TEXT NOT NULL,                       -- 'manual' | 'cron_incremental' | 'cron_full' | 'ops_unstick' | 'empty_retry'
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
  days_back INTEGER,                           -- NULL para Facebook (não tem esse parâmetro)
  empty_attempt INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'running',      -- 'running' | 'success' | 'failed' | 'skipped_lock'
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  records_fetched INTEGER,
  records_upserted INTEGER,
  is_suspected_partial BOOLEAN NOT NULL DEFAULT false,
  error_message TEXT,
  details JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS ix_sync_runs_user_started
  ON sync_runs (user_id, started_at DESC);

CREATE INDEX IF NOT EXISTS ix_sync_runs_source_trigger_started
  ON sync_runs (source, trigger, started_at DESC);

CREATE INDEX IF NOT EXISTS ix_sync_runs_running
  ON sync_runs (started_at) WHERE status = 'running';
