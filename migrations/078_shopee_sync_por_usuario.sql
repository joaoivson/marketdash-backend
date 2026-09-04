-- Migration 078: sync Shopee agendada para UM usuário
--
-- Contexto (04/09/2026). Os 24 jobs `shopee-sync-*h-brt` + o `-full-1h-brt`
-- estavam com `active = false` nos dois ambientes-alvo desta rodada. Eles não
-- quebraram: o último `cron.job_run_details` é `shopee-sync-12h-brt` em
-- 2026-08-05 15:00 UTC com status `succeeded`, e `sync_runs` não registra
-- nenhuma execução de `cron_incremental`/`cron_full` depois disso. Foram
-- DESLIGADOS, com os jobs saudáveis.
--
-- O efeito ficou invisível por 29 dias porque a tela só mostrava a data da
-- última sync em texto pequeno, e as duas contas com data recente tinham
-- rodado sync MANUAL — o que fazia o atraso parecer de 14 dias, não de 29.
--
-- Esta migration NÃO agenda nem desagenda nada. Ela só cria a função que
-- faltava: `trigger_shopee_sync` sincroniza TODO mundo, e não havia como
-- agendar uma conta só. O agendamento é manual e DIVERGE por ambiente —
-- blocos no fim do arquivo.

-- ---------------------------------------------------------------------------
-- Função: mesma coisa que trigger_shopee_sync, com `&user_id=` na URL.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.trigger_shopee_sync_user(
  p_user_id  int,
  sync_type  text DEFAULT 'incremental'
)
RETURNS bigint
LANGUAGE plpgsql
SECURITY DEFINER
AS $function$
DECLARE
  v_secret text;
  v_url    text;
  v_req_id bigint;
BEGIN
  IF sync_type NOT IN ('full', 'incremental') THEN
    RAISE EXCEPTION 'sync_type inválido: %', sync_type;
  END IF;

  IF p_user_id IS NULL OR p_user_id <= 0 THEN
    RAISE EXCEPTION 'p_user_id inválido: %', p_user_id;
  END IF;

  SELECT decrypted_secret INTO v_secret
  FROM vault.decrypted_secrets WHERE name = 'cron_shopee_secret';

  SELECT decrypted_secret INTO v_url
  FROM vault.decrypted_secrets WHERE name = 'backend_base_url';

  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE EXCEPTION 'cron_shopee_secret ou backend_base_url ausentes no Vault';
  END IF;

  -- `user_id` como query param é seguro AQUI e só aqui: quem chama é o pg_net,
  -- não o navegador. No frontend o `fetchWithAuth` injeta `?user_id=user_N` em
  -- toda request e sobrescreveria o valor, em silêncio.
  SELECT net.http_post(
    url     := v_url || '/api/v1/internal/cron/shopee-sync?type=' || sync_type
                     || '&user_id=' || p_user_id::text,
    headers := jsonb_build_object(
      'Content-Type',  'application/json',
      'X-Cron-Secret', v_secret,
      'X-Cron-Source', 'pg_cron-supabase'
    ),
    body    := jsonb_build_object(
      'triggered_at', now()::text,
      'sync_type',    sync_type,
      'user_id',      p_user_id
    ),
    timeout_milliseconds := 5000
  ) INTO v_req_id;

  RETURN v_req_id;
END;
$function$;

-- ---------------------------------------------------------------------------
-- ⚠️  AGENDAMENTO — MANUAL, e DIFERENTE em cada ambiente.
--
-- Não está aqui como DO block de propósito: rodar o arquivo inteiro no
-- ambiente errado religaria em homologação as 24 execuções horárias que
-- derrubaram o banco compartilhado em 20/07/2026.
-- ---------------------------------------------------------------------------
--
-- HOMOLOGAÇÃO (ytjpdvjuxtvxacredekk) — os 24 jobs de todo mundo ficam
-- DESLIGADOS; sobe só a conta do Luiz Fernando (user_id 9), que é a que
-- alimenta a validação com dado real:
--
--   SELECT cron.schedule('shopee-sync-luiz-incremental', '0 0-3,5-23 * * *',
--                        $$ SELECT public.trigger_shopee_sync_user(9, 'incremental'); $$);
--   SELECT cron.schedule('shopee-sync-luiz-full', '0 4 * * *',
--                        $$ SELECT public.trigger_shopee_sync_user(9, 'full'); $$);
--
--   -- 04:00 UTC fica de fora do incremental porque é o slot do full: os dois
--   -- na mesma hora disputariam o mesmo lock e um viraria `skipped_lock`.
--
-- PRODUÇÃO (iprdyorxqdiivthtcvxf) — religar os 24 + o full, como estavam
-- antes de 05/08. Não usa a função nova.
--
-- ⚠️  `UPDATE cron.job SET active = true` NÃO funciona no Supabase:
--     `ERROR: 42501: permission denied for table job`. A tabela tem RLS e o
--     DML direto não é liberado ao papel do SQL Editor. O caminho suportado é
--     `cron.alter_job()`, que é SECURITY DEFINER:
--
--   DO $$
--   DECLARE j record;
--   BEGIN
--     FOR j IN SELECT jobid, jobname FROM cron.job WHERE jobname LIKE 'shopee-sync-%' LOOP
--       PERFORM cron.alter_job(j.jobid, active := true);
--     END LOOP;
--   END $$;
--
--     Se nem o SELECT enxergar os jobs (a RLS filtra por
--     `username = current_user`, então vem VAZIO em vez de erro), recrie pelo
--     nome — `cron.schedule` faz upsert. O bloco abaixo é a migration 030
--     inteira, que é de onde estes jobs vieram:
--
--   DO $$
--   DECLARE hour_utc INT;
--   BEGIN
--     PERFORM cron.schedule('shopee-sync-full-1h-brt', '0 4 * * *',
--                           $cron$ SELECT public.trigger_shopee_sync('full'); $cron$);
--     FOR hour_utc IN 0..23 LOOP
--       IF hour_utc = 4 THEN CONTINUE; END IF;
--       PERFORM cron.schedule(
--         'shopee-sync-' || ((hour_utc + 21) % 24) || 'h-brt',
--         '0 ' || hour_utc || ' * * *',
--         $cron$ SELECT public.trigger_shopee_sync('incremental'); $cron$);
--     END LOOP;
--   END $$;
--
--   -- Conferir DEPOIS (a primeira hora é a que importa — ver migration 030):
--   SELECT jobname, active FROM cron.job WHERE jobname LIKE 'shopee-sync%';
--   SELECT trigger, status, count(*), max(started_at) FROM sync_runs
--    WHERE source='shopee' AND started_at > now() - interval '2 hours'
--    GROUP BY 1,2;
--
-- ⚠️  PRÉ-REQUISITO DOS DOIS: o caminho com `user_id` no endpoint despacha
-- `sync_shopee_user_task.delay(...)` e **não tem fallback inline** (o fallback
-- só existe no fan-out sem usuário). Sem worker Celery consumindo, a task é
-- aceita e nunca executa, em silêncio — o mesmo modo de falha de 28/07.
-- Validar com uma execução manual antes de confiar no agendamento.
