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
  v_jobname TEXT;
  v_sched TEXT;
BEGIN
  -- Remover jobs antigos (9-12h BRT que não cobrem todo o dia)
  PERFORM cron.unschedule('shopee-sync-9h-brt');
  PERFORM cron.unschedule('shopee-sync-10h-brt');
  PERFORM cron.unschedule('shopee-sync-11h-brt');
  PERFORM cron.unschedule('shopee-sync-12h-brt');

  -- Adicionar jobs para CADA HORA do dia (00-23h BRT)
  -- BRT = UTC - 3, então:
  -- 00:00 BRT = 03:00 UTC, 01:00 BRT = 04:00 UTC, ..., 23:00 BRT = 02:00 UTC (próximo dia)
  FOR hour_utc IN 3..23 LOOP
    v_jobname := 'shopee-sync-' || (hour_utc - 3) || 'h-brt';  -- 0h, 1h, ..., 20h
    v_sched := '0 ' || hour_utc || ' * * *';  -- Minuto 0 de cada hora UTC

    -- Pular 01:00 BRT (04:00 UTC) — já tem job de full sync
    IF hour_utc = 4 THEN
      CONTINUE;
    END IF;

    -- Remover se já existe
    BEGIN
      PERFORM cron.unschedule(v_jobname);
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;

    PERFORM cron.schedule(v_jobname, v_sched, $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$);
  END LOOP;

  -- Adicionar jobs para 21h-23h BRT (00-02h UTC do próximo dia)
  FOR hour_utc IN 0..2 LOOP
    v_jobname := 'shopee-sync-' || (hour_utc + 21) || 'h-brt';  -- 21h, 22h, 23h
    v_sched := '0 ' || hour_utc || ' * * *';  -- Minuto 0 de cada hora UTC

    -- Remover se já existe
    BEGIN
      PERFORM cron.unschedule(v_jobname);
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;

    PERFORM cron.schedule(v_jobname, v_sched, $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$);
  END LOOP;

END $$;
