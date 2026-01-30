-- Migration: Adicionar detalhes de status Cakto na tabela subscriptions
-- Data: 2026-01-29

ALTER TABLE subscriptions 
ADD COLUMN IF NOT EXISTS cakto_status VARCHAR(64),
ADD COLUMN IF NOT EXISTS cakto_offer_name VARCHAR(255),
ADD COLUMN IF NOT EXISTS cakto_due_date TIMESTAMP WITH TIME ZONE;

-- Índices auxiliares para consultas por status e vencimento
CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_status 
ON subscriptions(cakto_status);

CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_due_date 
ON subscriptions(cakto_due_date);
