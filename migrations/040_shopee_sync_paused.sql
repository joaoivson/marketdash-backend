-- Migration 040: pausa de sync Shopee para contas quebradas
--
-- Contas com credencial inválida (10020) ou que nunca sincronizaram e só falham
-- (ex.: System Error 10000) enchiam o painel: o cron horário reenfileirava toda hora
-- mesmo sem chance de sucesso. sync_paused_at != null → cron/fan-out pula a conta.
-- Limpa ao reconectar (upsert de credenciais).

ALTER TABLE shopee_integrations
  ADD COLUMN IF NOT EXISTS sync_paused_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS sync_pause_reason TEXT;

-- Backfill: só quem já falhou ou tem AppID claramente inválido.
-- Não pausa conta nova sem histórico (ainda pode sincronizar no próximo cron).
UPDATE shopee_integrations si
SET
  sync_paused_at = NOW(),
  sync_pause_reason = CASE
    WHEN si.app_id !~ '^[0-9]+$' THEN 'invalid_app_id_non_numeric'
    ELSE 'never_synced_chronic_failure'
  END
WHERE si.is_active = TRUE
  AND si.last_sync_at IS NULL
  AND si.sync_paused_at IS NULL
  AND (
    si.app_id !~ '^[0-9]+$'
    OR EXISTS (
      SELECT 1
      FROM sync_runs sr
      WHERE sr.user_id = si.user_id
        AND sr.source = 'shopee'
        AND sr.status = 'failed'
    )
  );
