-- Migration: Adicionar coluna clicks na tabela ad_spends
-- Data: 2026-01-31

ALTER TABLE ad_spends 
ADD COLUMN IF NOT EXISTS clicks INTEGER DEFAULT 0;

-- Atualizar registros existentes para terem 0 cliques em vez de NULL, se necessário
UPDATE ad_spends SET clicks = 0 WHERE clicks IS NULL;
