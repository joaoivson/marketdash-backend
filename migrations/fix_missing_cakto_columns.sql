-- Migration: Corrigir colunas faltantes de Cakto na tabela subscriptions
-- Data: 2026-01-30

ALTER TABLE subscriptions 
ADD COLUMN IF NOT EXISTS last_validation_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS cakto_customer_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS cakto_transaction_id VARCHAR(255),
ADD COLUMN IF NOT EXISTS cakto_status VARCHAR(64),
ADD COLUMN IF NOT EXISTS cakto_offer_name VARCHAR(255),
ADD COLUMN IF NOT EXISTS cakto_due_date TIMESTAMP WITH TIME ZONE,
ADD COLUMN IF NOT EXISTS cakto_subscription_status VARCHAR(64),
ADD COLUMN IF NOT EXISTS cakto_payment_status VARCHAR(64),
ADD COLUMN IF NOT EXISTS cakto_payment_method VARCHAR(64);

-- Criar índices para melhor performance
CREATE INDEX IF NOT EXISTS idx_subscriptions_last_validation ON subscriptions(last_validation_at);
CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_customer_id ON subscriptions(cakto_customer_id);
CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_status ON subscriptions(cakto_status);
CREATE INDEX IF NOT EXISTS idx_subscriptions_cakto_due_date ON subscriptions(cakto_due_date);
