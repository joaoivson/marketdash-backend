-- Migration 027: cron do Facebook usa Authorization: Bearer (não X-Cron-Secret).
--
-- BUG: a migration 020 já trocou o header do cron do SHOPEE de X-Cron-Secret para
-- Authorization: Bearer porque um proxy no caminho (Cloudflare / Coolify Traefik)
-- estava STRIPPANDO o header X-Cron-Secret — o backend recebia a request sem o
-- secret e respondia 401. Mas a migration 022 (trigger_facebook_sync), criada
-- DEPOIS da 020, reintroduziu o X-Cron-Secret. Resultado: o cron HORÁRIO do
-- Facebook chega no backend sem secret → 401 → sync_all_facebook_users_task nunca
-- é enfileirada → o gatilho automático "não dispara sozinho".
--   (O botão "Atualizar" funciona porque usa a rota autenticada /facebook/sync,
--    que não passa pelo cron-secret. Por isso o sync manual roda e o horário não.)
--
-- O endpoint /internal/cron/facebook-sync aceita ambos os headers, então a troca é
-- backward-compatible e NÃO exige redeploy do backend — só reaplicar esta migration.
--
-- Reaproveita os mesmos secrets do Vault (cron_shopee_secret, backend_base_url).

-- 1) Recria a função wrapper enviando Authorization: Bearer (igual à 020 do Shopee).
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
      'Authorization', 'Bearer ' || v_secret,
      'X-Cron-Source', 'pg_cron-supabase'
    ),
    body    := jsonb_build_object('triggered_at', now()::text),
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$$;

-- 2) Garante o agendamento horário (idempotente). Self-sufficient caso a 023 não
--    tenha sido aplicada: remove nomes antigos e (re)agenda de hora em hora.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'facebook-sync-730am-brt') THEN
    PERFORM cron.unschedule('facebook-sync-730am-brt');
  END IF;
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'facebook-sync-hourly') THEN
    PERFORM cron.unschedule('facebook-sync-hourly');
  END IF;
END $$;

SELECT cron.schedule(
  'facebook-sync-hourly',
  '0 * * * *',
  $cron$ SELECT public.trigger_facebook_sync(); $cron$
);
