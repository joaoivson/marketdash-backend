-- Migration: Adicionar campos de Cakto na tabela subscriptions
-- Data: 2026-01-26

ALTER TABLE subscriptions 
ADD COLUMN IF NOT EXISTS last_validation_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS cakto_customer_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS cakto_transaction_id VARCHAR(255);

-- Criar índices para melhor performance
CREATE INDEX IF NOT EXISTS idx_subscriptions_last_validation 
ON subscriptions(last_validation_at);

CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_customer_id 
ON subscriptions(cakto_customer_id);

-- Atualizar is_active padrão para False (assinatura requer ativação)
ALTER TABLE subscriptions 
ALTER COLUMN is_active SET DEFAULT FALSE;
