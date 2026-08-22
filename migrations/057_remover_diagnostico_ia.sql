-- Migration 057: remover o Diagnóstico IA.
--
-- A feature saiu do produto — rotas, serviços, modelos e prompts foram
-- apagados do código. Estas três tabelas nasceram na 043 (e ganharam o índice
-- parcial da 044) e existem SOMENTE em homologação: nunca foram aplicadas em
-- produção, então lá não há nada para derrubar e rodar isto é no-op.
--
-- Ordem obrigatória por causa das FKs para `ai_diagnostics`:
-- `ai_credit_ledger` (diagnostic_id) e `ai_diagnostic_messages` (diagnostic_id)
-- caem primeiro; a tabela-pai por último. CASCADE leva junto o índice
-- `ux_ai_diagnostics_gerando_por_usuario` da 044 e quaisquer outras views ou
-- constraints dependentes.
--
-- Tudo num BEGIN: ou o esquema fica limpo, ou fica exatamente como estava.

BEGIN;

DROP TABLE IF EXISTS ai_credit_ledger CASCADE;
DROP TABLE IF EXISTS ai_diagnostic_messages CASCADE;
DROP TABLE IF EXISTS ai_diagnostics CASCADE;

COMMIT;
