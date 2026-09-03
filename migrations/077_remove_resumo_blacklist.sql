-- 077: Remoção do Resumo diário por WhatsApp e da Blacklist de números
--      (rodada de correções — spec §9.1/§9.2).
--
-- O resumo diário (opt-in por código, sessão global do MarketDash, cron das
-- 9h BRT) e a blacklist saíram do produto POR COMPLETO: rotas, services,
-- models e o endpoint interno /cron/whatsapp-resumo já não existem no código.
-- Esta migration desmonta o que vivia no banco: o agendamento do pg_cron, a
-- função que ele chamava (criados na 046) e as tabelas (045 e 060/067).
--
-- ⚠️ PROTOCOLO: aplicar em HML **E** PRODUÇÃO, junto do deploy que remove o
-- código. Ordem importa dentro do arquivo: primeiro o cron (senão ele segue
-- chamando um endpoint que agora responde 404 a cada dia), depois função e
-- tabelas. Tudo idempotente — reaplicar não faz nada.
--
-- Dados: whatsapp_optins/whatsapp_envios/blacklist_numeros são descartados de
-- propósito (feature removida, sem sucessora). Nenhuma outra tabela referencia
-- essas três.

BEGIN;

-- 1) Desagenda o job do resumo (criado na 046). Guard de existência: chamar
--    cron.unschedule de job inexistente levanta erro e abortaria a transação.
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM cron.job WHERE jobname = 'whatsapp-resumo-9am-brt') THEN
    PERFORM cron.unschedule('whatsapp-resumo-9am-brt');
  END IF;
END $$;

-- 2) A função que o job chamava via pg_net.
DROP FUNCTION IF EXISTS public.trigger_whatsapp_resumo();

-- 3) As tabelas das duas features.
DROP TABLE IF EXISTS whatsapp_optins;
DROP TABLE IF EXISTS whatsapp_envios;
DROP TABLE IF EXISTS blacklist_numeros;

COMMIT;
