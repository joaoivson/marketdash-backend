-- Migration 027: Shopee sync dividido (completo madrugada + incremental horário)
--
-- Novo padrão:
-- - 01:00 BRT (04:00 UTC): reconcile COMPLETO 90 dias + reprocesso categoria/canal/attribution
-- - 09-12h BRT: incremental dos últimos ~3 dias (cobre atraso da Shopee)
--
-- A função trigger_shopee_sync já existe (migration 018) e reusa o mesmo método.
-- Aqui adicionamos um tipo de parametrização: tipo='full' (90d) vs tipo='incremental' (3d).
--
-- Para compatibilidade: manter jobs antigos (9-12h) como incremental, adicionar novo job madrugada.

DO $$
DECLARE
  j record;
BEGIN
  -- Remover jobs antigos 9-12h e substituir por versão incremental
  FOR j IN
    SELECT * FROM (VALUES
      ('shopee-sync-9h-brt', '0 12 * * *'),
      ('shopee-sync-10h-brt', '0 13 * * *'),
      ('shopee-sync-11h-brt', '0 14 * * *'),
      ('shopee-sync-12h-brt', '0 15 * * *')
    ) AS t(jobname, sched)
  LOOP
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = j.jobname) THEN
      PERFORM cron.unschedule(j.jobname);
    END IF;
    -- Atualizar com call incrementa (tipo='incremental' será capturado pela API)
    PERFORM cron.schedule(j.jobname, j.sched, $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$);
  END LOOP;

  -- Novo job: madrugada para reconcile COMPLETO
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'shopee-sync-full-1h-brt') THEN
    PERFORM cron.unschedule('shopee-sync-full-1h-brt');
  END IF;
  PERFORM cron.schedule(
    'shopee-sync-full-1h-brt',
    '0 4 * * *',  -- 01:00 BRT = 04:00 UTC
    $cron$ SELECT public.trigger_shopee_sync('full'); $cron$
  );
END $$;
