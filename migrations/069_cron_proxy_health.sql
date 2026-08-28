-- 069: sonda de saúde do pool de proxies — de hora em hora.
--
-- ⚠️ Rode SÓ no banco do ambiente que ENVIA (hml durante o desenvolvimento;
-- produção no GA), e só depois de existir proxy cadastrado — com o pool vazio
-- a sonda é um no-op por hora.
--
-- Por que de hora em hora e não 1×/dia como o snapshot de grupos: aqui a
-- chamada externa é um GET por proxy contra um eco de IP e o trabalho no banco
-- é um UPDATE por linha do pool. Não é o padrão de 20/07 (sync pesado horário
-- num banco compartilhado) — e um IP morto descoberto no dia seguinte já
-- custou um lote inteiro de envio.

CREATE OR REPLACE FUNCTION public.trigger_proxy_health()
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
  FROM vault.decrypted_secrets WHERE name = 'cron_shopee_secret';
  SELECT decrypted_secret INTO v_url
  FROM vault.decrypted_secrets WHERE name = 'backend_base_url';

  IF v_secret IS NULL OR v_url IS NULL THEN
    RAISE WARNING 'trigger_proxy_health: segredos ausentes no Vault';
    RETURN NULL;
  END IF;

  SELECT net.http_post(
    url := v_url || '/api/v1/internal/cron/proxy-health',
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
  PERFORM cron.unschedule('proxy-health-horario');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;

SELECT cron.schedule(
  'proxy-health-horario',
  '17 * * * *',           -- 17 min de cada hora: fora do minuto 0, onde os
                          -- outros crons se acumulam
  $$SELECT public.trigger_proxy_health()$$
);
