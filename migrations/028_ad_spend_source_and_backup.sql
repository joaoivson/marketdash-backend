-- Migration 028: AdSpend ganha ORIGEM (manual/meta) + BACKUP one-time dos lançamentos manuais.
--
-- Contexto (rodada 5, frente C): o lançamento manual de "Custos de Anúncios" foi removido; o
-- gasto E os cliques de anúncios passam a vir 100% da integração Meta, gravados na MESMA tabela
-- ad_spends (que o Dashboard lê). A integração reconstrói as linhas source='meta'; o Meta é
-- autoritativo nos dias que cobre (substitui o manual daquele dia), mas o manual de dias
-- ANTERIORES à cobertura permanece. Pra não zerar o que o pessoal preencheu, guardamos um
-- backup one-time antes da virada.

-- 1) Coluna de origem. Dados históricos (preenchidos à mão) ficam como 'manual'.
ALTER TABLE ad_spends ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'manual';

-- 2) Backup one-time de tudo que existe hoje (todos manuais). NÃO recria se já houver backup,
--    pra não sobrescrever o snapshot original em reaplicações. (AS SELECT * é portável; AS TABLE
--    só existe no PG14+.)
CREATE TABLE IF NOT EXISTS ad_spends_manual_backup AS SELECT * FROM ad_spends;
