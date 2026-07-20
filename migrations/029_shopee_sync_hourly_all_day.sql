-- Migration 029: Shopee sync de hora em hora o dia inteiro (00-23h BRT)
--
-- Novo padrão após QA feedback:
-- - 01:00 BRT (04:00 UTC): reconcile COMPLETO 90 dias
-- - 00h-23h BRT (exceto 01h): sync incremental 3 dias (cada hora)
--
-- Motivo: Shopee atualiza em horários variados. Alguns dias passa um horário
-- específico (ex: 15h) e não pega entre 09-12h. Sincronizar cada hora garante
-- que sempre pega os dados mais recentes.

DO $$
DECLARE
  hour_utc INT;
  jobname TEXT;
  sched TEXT;
  j record;
BEGIN
  -- Remover jobs antigos (9-12h BRT que não cobrem todo o dia)
  FOR j IN
    SELECT * FROM (VALUES
      ('shopee-sync-9h-brt'),
      ('shopee-sync-10h-brt'),
      ('shopee-sync-11h-brt'),
      ('shopee-sync-12h-brt')
    ) AS t(name)
  LOOP
    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = j.name) THEN
      PERFORM cron.unschedule(j.name);
    END IF;
  END LOOP;

  -- Adicionar jobs para CADA HORA do dia (00-23h BRT)
  -- BRT = UTC - 3, então:
  -- 00:00 BRT = 03:00 UTC, 01:00 BRT = 04:00 UTC, ..., 23:00 BRT = 02:00 UTC (próximo dia)
  FOR hour_utc IN 3..23 LOOP
    jobname := 'shopee-sync-' || (hour_utc - 3) || 'h-brt';  -- 0h, 1h, ..., 20h
    sched := '0 ' || hour_utc || ' * * *';  -- Minuto 0 de cada hora UTC

    -- Pular 01:00 BRT (04:00 UTC) — já tem job de full sync
    IF hour_utc = 4 THEN
      CONTINUE;
    END IF;

    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = jobname) THEN
      PERFORM cron.unschedule(jobname);
    END IF;

    PERFORM cron.schedule(
      jobname,
      sched,
      $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$
    );
  END LOOP;

  -- Adicionar jobs para 21h-23h BRT (00-02h UTC do próximo dia)
  FOR hour_utc IN 0..2 LOOP
    jobname := 'shopee-sync-' || (hour_utc + 21) || 'h-brt';  -- 21h, 22h, 23h
    sched := '0 ' || hour_utc || ' * * *';  -- Minuto 0 de cada hora UTC

    IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = jobname) THEN
      PERFORM cron.unschedule(jobname);
    END IF;

    PERFORM cron.schedule(
      jobname,
      sched,
      $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$
    );
  END LOOP;

END $$;
