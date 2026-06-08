-- Migration 023: múltiplas contas de anúncio + sync do Facebook a cada 1 hora.

-- 1) Coluna para múltiplas contas selecionadas (JSON: ["act_123","act_456"]).
ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_json TEXT;

-- 2) Reagendar o pg_cron do Facebook para DE HORA EM HORA (antes era diário às 10h30 UTC).
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'facebook-sync-730am-brt') THEN
    PERFORM cron.unschedule('facebook-sync-730am-brt');
  END IF;
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'facebook-sync-hourly') THEN
    PERFORM cron.unschedule('facebook-sync-hourly');
  END IF;
END $$;

-- A cada hora, no minuto 0. Reaproveita a função public.trigger_facebook_sync() da migration 022.
SELECT cron.schedule(
  'facebook-sync-hourly',
  '0 * * * *',
  $cron$ SELECT public.trigger_facebook_sync(); $cron$
);
