-- Migration 022: pg_cron + pg_net para sync diário do Facebook Ads.
--
-- pg_cron dispara HTTP POST para /api/v1/internal/cron/facebook-sync no backend,
-- que enfileira sync_all_facebook_users_task no Celery worker.
--
-- Schedule: 7h30 BRT = 10h30 UTC (deslocado do Shopee às 10h para não concorrer).
--
-- Reutiliza os MESMOS secrets do Vault da migration 018:
--   cron_shopee_secret  (== CRON_SECRET; o endpoint interno valida o mesmo secret)
--   backend_base_url
-- Se ainda não existirem, criar com vault.create_secret (ver migration 018).

-- 1) Extensões (idempotente)
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

-- 2) Função wrapper que lê secrets do Vault e dispara o HTTP POST
CREATE OR REPLACE FUNCTION public.trigger_facebook_sync()
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_secret text;
  v_url    text;
  v_req_id bigint;
BEGIN
  SELECT decrypted_secret INTO v_secret
  FROM vault.decrypted_secrets
  WHERE name = 'cron_shopee_secret';

  SELECT decrypted_secret INTO v_url
  FROM vault.decrypted_secrets
  WHERE name = 'backend_base_url';

  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE EXCEPTION 'cron_shopee_secret ou backend_base_url ausentes no Vault — rodar vault.create_secret primeiro';
  END IF;

  SELECT net.http_post(
    url     := v_url || '/api/v1/internal/cron/facebook-sync',
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'X-Cron-Secret', v_secret,
      'X-Cron-Source', 'pg_cron-supabase'
    ),
    body    := jsonb_build_object('triggered_at', now()::text),
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$$;

-- 3) Remover schedule antigo se existir (idempotência ao reaplicar)
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'facebook-sync-730am-brt') THEN
    PERFORM cron.unschedule('facebook-sync-730am-brt');
  END IF;
END $$;

-- 4) Agendar: 7h30 BRT = 10h30 UTC, todos os dias
SELECT cron.schedule(
  'facebook-sync-730am-brt',
  '30 10 * * *',
  $cron$ SELECT public.trigger_facebook_sync(); $cron$
);
