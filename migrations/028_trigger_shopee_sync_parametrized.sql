-- Migration 028: Parametrizar trigger_shopee_sync com tipo ('full' vs 'incremental')
--
-- Atualiza a função para aceitar tipo de sincronização:
-- - 'full': reconcile 90 dias + reprocesso
-- - 'incremental': últimos 3 dias (padrão)

DROP FUNCTION IF EXISTS public.trigger_shopee_sync();

CREATE OR REPLACE FUNCTION public.trigger_shopee_sync(sync_type text DEFAULT 'incremental')
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_secret text;
  v_url    text;
  v_req_id bigint;
BEGIN
  -- Validar tipo
  IF sync_type NOT IN ('full', 'incremental') THEN
    RAISE EXCEPTION 'sync_type inválido: %', sync_type;
  END IF;

  SELECT decrypted_secret INTO v_secret
  FROM vault.decrypted_secrets
  WHERE name = 'cron_shopee_secret';

  SELECT decrypted_secret INTO v_url
  FROM vault.decrypted_secrets
  WHERE name = 'backend_base_url';

  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE EXCEPTION 'cron_shopee_secret ou backend_base_url ausentes no Vault';
  END IF;

  SELECT net.http_post(
    url     := v_url || '/api/v1/internal/cron/shopee-sync?type=' || sync_type,
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'X-Cron-Secret', v_secret,
      'X-Cron-Source', 'pg_cron-supabase'
    ),
    body    := jsonb_build_object('triggered_at', now()::text, 'sync_type', sync_type),
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$$;
