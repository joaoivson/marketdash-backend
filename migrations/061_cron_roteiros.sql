-- 061: agenda o tick do motor de roteiros — a cada 5 minutos.
--
-- Mesmo padrão da 046 (pg_cron + pg_net + Vault). O tick é BARATO: um UPDATE
-- sobre índice parcial que normalmente devolve 0 linhas (nada a ver com o
-- sync horário que derrubou o banco em 20/07 — aquilo era sync pesado).
--
-- ⚠️ Rode SÓ no banco do ambiente que deve enviar (hml durante o
-- desenvolvimento; produção somente no GA da fase). Agendar nos DOIS bancos
-- com o mesmo servidor WAHA = mensagem duplicada em grupo alheio.

CREATE OR REPLACE FUNCTION public.trigger_roteiros_tick()
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
  FROM vault.decrypted_secrets
  WHERE name = 'cron_shopee_secret';   -- mesmo CRON_SECRET de todas as rotas internas

  SELECT decrypted_secret INTO v_url
  FROM vault.decrypted_secrets
  WHERE name = 'backend_base_url';

  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE WARNING 'trigger_roteiros_tick: segredos ausentes no Vault';
    RETURN NULL;
  END IF;

  SELECT net.http_post(
    url := v_url || '/api/v1/internal/cron/roteiros',
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
  PERFORM cron.unschedule('roteiros-tick-5min');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;

SELECT cron.schedule(
  'roteiros-tick-5min',
  '*/5 * * * *',
  $$SELECT public.trigger_roteiros_tick()$$
);
