-- 046: agenda o resumo diário no WhatsApp — 9h de Brasília.
--
-- Mesmo padrão do 018 (Shopee) e do 023 (Facebook): pg_cron chama um endpoint
-- HTTP da API via pg_net, com o secret vindo do Vault.
--
-- ⚠️ Rode SÓ no banco do ambiente que deve enviar. Agendar isto em homologação
-- E em produção com o mesmo número da Evolution faz a afiliada receber o
-- resumo duas vezes — e o índice único de whatsapp_envios não protege disso,
-- porque são bancos diferentes.

-- 1) Função que dispara a chamada
CREATE OR REPLACE FUNCTION public.trigger_whatsapp_resumo()
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
    RAISE EXCEPTION 'cron_shopee_secret ou backend_base_url ausentes no Vault';
  END IF;

  SELECT net.http_post(
    url     := v_url || '/api/v1/internal/cron/whatsapp-resumo',
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'Authorization', 'Bearer ' || v_secret,
      'X-Cron-Secret', v_secret,
      'X-Cron-Source', 'pg_cron-supabase'
    ),
    body    := jsonb_build_object('triggered_at', now()::text),
    -- A API responde 202 na hora e manda o lote num BackgroundTask; 5s sobra.
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$$;

-- 2) Idempotência ao reaplicar
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'whatsapp-resumo-9am-brt') THEN
    PERFORM cron.unschedule('whatsapp-resumo-9am-brt');
  END IF;
END $$;

-- 3) 9h BRT = 12h UTC. O Brasil não tem horário de verão desde 2019, então o
--    deslocamento é fixo em -3 e esta conta não muda ao longo do ano.
SELECT cron.schedule(
  'whatsapp-resumo-9am-brt',
  '0 12 * * *',
  $cron$ SELECT public.trigger_whatsapp_resumo(); $cron$
);
