-- 064: retrato diário dos grupos + reconciliação de sessões órfãs — 03:00 BRT.
--
-- ⚠️ Rode SÓ no banco do ambiente que envia (hml durante o desenvolvimento;
-- produção apenas no GA). Dois ambientes com o mesmo servidor WAHA fariam a
-- reconciliação de um derrubar as sessões do outro — o prefixo de ambiente no
-- nome protege, mas cron duplicado é desperdício garantido.
--
-- Cadência 1×/dia: sync de hora em hora foi o que derrubou o banco em 20/07.

CREATE OR REPLACE FUNCTION public.trigger_grupos_snapshot()
RETURNS BIGINT
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  v_secret TEXT;
  v_url    TEXT;
  v_req_id BIGINT;
BEGIN
  SELECT decrypted_secret INTO v_secret
  FROM vault.decrypted_secrets WHERE name = 'cron_shopee_secret';
  SELECT decrypted_secret INTO v_url
  FROM vault.decrypted_secrets WHERE name = 'backend_base_url';

  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE WARNING 'trigger_grupos_snapshot: segredos ausentes no Vault';
    RETURN NULL;
  END IF;

  SELECT net.http_post(
    url := v_url || '/api/v1/internal/cron/grupos-snapshot',
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Authorization', 'Bearer ' || v_secret,
      'X-Cron-Secret', v_secret
    ),
    body := '{}'::jsonb,
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$$;

DO $$
BEGIN
  PERFORM cron.unschedule('grupos-snapshot-3am-brt');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;

SELECT cron.schedule(
  'grupos-snapshot-3am-brt',
  '0 6 * * *',            -- 06:00 UTC = 03:00 BRT
  $$SELECT public.trigger_grupos_snapshot()$$
);
