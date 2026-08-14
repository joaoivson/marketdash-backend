-- Migration 050: status_active_since em campaigns
--
-- O fix da campanha "zumbi de orçamento vitalício esgotado" (ver fix
-- campanha ativa/orçamento esgotado) dava benefício da dúvida a campanhas
-- sem insight recente usando `created_at` — mas isso quebra o caso de uma
-- campanha ANTIGA que estava PAUSADA e acabou de ser REATIVADA: created_at
-- continua sendo de semanas atrás, então ela cairia no mesmo balde do
-- zumbi (excluída da contagem) mesmo estando genuinamente ativa de novo,
-- só ainda sem insight acumulado.
--
-- status_active_since marca quando o `effective_status` da campanha
-- transicionou PARA "ACTIVE" pela última vez (seja na criação, seja numa
-- reativação depois de pausada) — é essa data, não `created_at`, que deve
-- servir de âncora pro período de carência.
--
-- Aditiva: coluna nova, nullable, não quebra nada existente. NULL nas
-- linhas já existentes até o próximo sync detectar uma transição (o
-- código já cai de volta pra `created_at` nesse meio tempo).

ALTER TABLE campaigns
  ADD COLUMN IF NOT EXISTS status_active_since TIMESTAMPTZ;
