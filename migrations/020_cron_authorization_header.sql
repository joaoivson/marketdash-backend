-- Migration 020: trocar header X-Cron-Secret por Authorization: Bearer
--
-- Algum proxy no caminho (Cloudflare ou Coolify Traefik) estava strippando
-- o header X-Cron-Secret, fazendo o backend receber a request sem o secret e
-- retornar 401. Authorization e header padrao HTTP — passa por qualquer proxy.
--
-- O endpoint /internal/cron/shopee-sync no backend aceita ambos os headers,
-- entao essa mudanca e backward-compatible.

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
      'Authorization', 'Bearer ' || v_secret,
      'X-Cron-Source', 'pg_cron-supabase'
    ),
    body    := jsonb_build_object('triggered_at', now()::text),
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$$;
