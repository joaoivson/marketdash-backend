-- Migration 030: Shopee sync de hora em hora — versão SEGURA
--
-- ⚠️  PRÉ-REQUISITO OBRIGATÓRIO (incidente 20/07/2026):
--   O backend apontado pelo Vault (backend_base_url) PRECISA estar rodando o
--   commit com o fix "sem retry chain em sync incremental" em
--   app/tasks/shopee_tasks.py. Sem esse fix, tasks vazias reagendam retries
--   de hora em hora e, somadas ao cron horário, derrubam o banco.
--
-- ⚠️  NÃO está na lista do scripts/apply_migrations.py de propósito.
--   Aplicar manualmente, fora de horário de pico, e monitorar a 1ª hora.
--
-- Agenda: incremental (3 dias) a cada hora, exceto 01:00 BRT (full 90d, já
-- existe como shopee-sync-full-1h-brt) e mantendo os jobs existentes de
-- 7h/9h-12h BRT como estão.

DO $$
DECLARE
  hour_utc INT;
  v_jobname TEXT;
  v_sched TEXT;
BEGIN
  FOR hour_utc IN 0..23 LOOP
    -- 04:00 UTC = 01:00 BRT → full sync já agendado (shopee-sync-full-1h-brt)
    IF hour_utc = 4 THEN
      CONTINUE;
    END IF;

    -- Nome pelo horário BRT (UTC-3), ex.: 13 UTC → 10h BRT
    v_jobname := 'shopee-sync-' || ((hour_utc + 21) % 24) || 'h-brt';
    v_sched := '0 ' || hour_utc || ' * * *';

    -- Idempotente: remove se já existe (inclusive os jobs 7am/9h-12h antigos,
    -- que são substituídos pela versão incremental de mesmo horário)
    BEGIN
      PERFORM cron.unschedule(v_jobname);
    EXCEPTION WHEN OTHERS THEN
      NULL;
    END;

    PERFORM cron.schedule(v_jobname, v_sched, $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$);
  END LOOP;
END $$;
