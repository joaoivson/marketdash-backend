-- Migration 018: Substituir Celery Beat por pg_cron + pg_net para sync Shopee.
--
-- pg_cron dispara HTTP POST para /api/v1/internal/cron/shopee-sync no backend FastAPI,
-- que enfileira sync_all_shopee_users_task no Celery worker.
--
-- Schedule: 7h BRT = 10h UTC fixo (Brasil sem horário de verão desde 2019).
--
-- Pré-requisitos (rodar UMA VEZ via SQL Editor logado como superuser, fora desta migration):
--   SELECT vault.create_secret('<openssl rand -hex 32>', 'cron_shopee_secret');
--   SELECT vault.create_secret('https://api.marketdash.com.br', 'backend_base_url');
--
-- Para rotacionar/atualizar:
--   UPDATE vault.secrets SET secret = '<novo>' WHERE name = 'cron_shopee_secret';

-- 1) Extensões
CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

-- 2) Função wrapper que lê secrets do Vault e dispara o HTTP POST
CREATE OR REPLACE FUNCTION public.trigger_shopee_sync()
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
    url     := v_url || '/api/v1/internal/cron/shopee-sync',
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
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'shopee-sync-7am-brt') THEN
    PERFORM cron.unschedule('shopee-sync-7am-brt');
  END IF;
END $$;

-- 4) Agendar: 7h BRT = 10h UTC, todos os dias
SELECT cron.schedule(
  'shopee-sync-7am-brt',
  '0 10 * * *',
  $cron$ SELECT public.trigger_shopee_sync(); $cron$
);
