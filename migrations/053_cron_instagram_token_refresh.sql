-- Migration 053: pg_cron diário para renovar os tokens do Instagram.
--
-- O token longo do Business Login for Instagram dura 60 dias e SÓ pode ser
-- renovado enquanto ainda está válido. Se vencer, a aluna precisa refazer o
-- login manualmente — e a automação dela fica muda até lá. Por isso o cron roda
-- todo dia e renova tudo que vence em menos de 10 dias: mesmo com o backend fora
-- do ar por alguns dias, ainda sobra janela.
--
-- Header Authorization: Bearer — NÃO X-Cron-Secret (ver migration 027: um proxy
-- no caminho removia o header e o backend respondia 401).
--
-- Reutiliza os secrets do Vault da migration 018 (cron_shopee_secret, backend_base_url).

CREATE EXTENSION IF NOT EXISTS pg_cron;
CREATE EXTENSION IF NOT EXISTS pg_net WITH SCHEMA extensions;

CREATE OR REPLACE FUNCTION public.trigger_instagram_token_refresh()
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
    url     := v_url || '/api/v1/internal/cron/instagram-token-refresh',
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

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'instagram-token-refresh-diario') THEN
    PERFORM cron.unschedule('instagram-token-refresh-diario');
  END IF;
END $$;

-- 05h15 UTC (02h15 BRT) — horário morto, sem concorrer com Shopee (10h) nem
-- Facebook (de hora em hora no minuto 30).
SELECT cron.schedule(
  'instagram-token-refresh-diario',
  '15 5 * * *',
  $cron$ SELECT public.trigger_instagram_token_refresh(); $cron$
);
