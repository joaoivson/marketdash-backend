-- Migration: Adicionar colunas genéricas de provider ao lado das colunas cakto_*
-- Permite alternar entre Cakto e Kiwify via feature flag sem perder dados

ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider VARCHAR(32) DEFAULT 'cakto';
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_customer_id VARCHAR(255);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_transaction_id VARCHAR(255);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_status VARCHAR(64);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_offer_name VARCHAR(255);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_due_date TIMESTAMP WITH TIME ZONE;
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_subscription_status VARCHAR(64);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_payment_status VARCHAR(64);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_payment_method VARCHAR(64);
ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS provider_order_id VARCHAR(255);

-- Backfill dados existentes do Cakto
UPDATE subscriptions SET
  provider = 'cakto',
  provider_customer_id = cakto_customer_id,
  provider_transaction_id = cakto_transaction_id,
  provider_status = cakto_status,
  provider_offer_name = cakto_offer_name,
  provider_due_date = cakto_due_date,
  provider_subscription_status = cakto_subscription_status,
  provider_payment_status = cakto_payment_status,
  provider_payment_method = cakto_payment_method
WHERE cakto_customer_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_subscriptions_provider ON subscriptions(provider);
CREATE INDEX IF NOT EXISTS idx_subscriptions_provider_customer_id ON subscriptions(provider_customer_id);
