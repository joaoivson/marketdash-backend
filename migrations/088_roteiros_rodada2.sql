-- 088: rodada 2 de Roteiros (18/09/2026).
--
-- Duas coisas sem relação entre si, na mesma migration porque a rodada é uma
-- só e duas migrations seriam dois passos no runbook para o mesmo deploy.
--
-- ⚠️ `whatsapp_grupos` JÁ EXISTE nos dois ambientes, então esta migration não
-- cria tabela — o risco de `create_all` criar em produção sem RLS (que vale
-- para toda tabela nova) não se aplica aqui.

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. Descrição do grupo
--
-- O sync passa a trazê-la junto com nome e foto, e o passo de ação
-- `alterar_descricao` passa a gravá-la no registro local NA HORA. Sem a
-- coluna, o painel exibia o valor de antes e a afiliada não tinha como saber
-- se o passo funcionou — justamente agora que o roteiro altera descrição.
-- ─────────────────────────────────────────────────────────────────────────────
ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS descricao TEXT;

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. Tick do motor de roteiros: de 5 em 5 minutos para 1 em 1 minuto
--
-- Mesma função `trigger_roteiros_tick` da 061 — só a cadência muda. O tick é
-- um UPDATE sobre índice parcial (`ix_roteiro_execucoes_tick`) que normalmente
-- devolve ZERO linhas; nada a ver com o sync horário que derrubou o banco
-- compartilhado em 20/07, que era sync pesado.
--
-- Por que 1 minuto: a rodada 2 estabelece que roteiro NUNCA envia atrasado —
-- passou de 3 minutos do horário, a mensagem falha em vez de sair fora de
-- hora. Com tick de 5 minutos, a própria cadência gastaria a tolerância
-- inteira e nada sairia.
--
-- ⚠️ O unschedule vai dentro de bloco com EXCEPTION porque PRODUÇÃO NÃO TEM O
-- JOB (medido em 06/09/2026 — a 061 nunca rodou lá). Sem o guard, a migration
-- aborta em produção. Pelo mesmo motivo, promover o módulo exige a 061 ANTES
-- da 088: é ela que cria `trigger_roteiros_tick`.
-- ─────────────────────────────────────────────────────────────────────────────
DO $$
BEGIN
  PERFORM cron.unschedule('roteiros-tick-5min');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;

-- Idempotência: reaplicar a migration não pode falhar por job já existente.
DO $$
BEGIN
  PERFORM cron.unschedule('roteiros-tick-1min');
EXCEPTION WHEN OTHERS THEN
  NULL;
END $$;

SELECT cron.schedule(
  'roteiros-tick-1min',
  '* * * * *',
  $$SELECT public.trigger_roteiros_tick()$$
);
