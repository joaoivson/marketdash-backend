-- Migration 026: janelas extras de sync Shopee.
--
-- A Shopee atualiza as comissões 1x/dia, mas em HORÁRIO VARIÁVEL. Rodar em
-- 9h, 10h, 11h e 12h BRT (12h, 13h, 14h, 15h UTC) cobre a incerteza do horário —
-- não é sincronizar 4x de propósito, é garantir a captura do update diário.
-- Mantém o job das 7h BRT (migration 018) como primeira tentativa.
--
-- Reusa public.trigger_shopee_sync() (migration 018). Idempotente.

DO $$
DECLARE
  j record;
BEGIN
  FOR j IN
    SELECT * FROM (VALUES
      ('shopee-sync-9h-brt',  '0 12 * * *'),
      ('shopee-sync-10h-brt', '0 13 * * *'),
      ('shopee-sync-11h-brt', '0 14 * * *'),
      ('shopee-sync-12h-brt', '0 15 * * *')
    ) AS t(jobname, sched)
  LOOP
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = j.jobname) THEN
      PERFORM cron.unschedule(j.jobname);
    END IF;
    PERFORM cron.schedule(j.jobname, j.sched, $cron$ SELECT public.trigger_shopee_sync(); $cron$);
  END LOOP;
END $$;
