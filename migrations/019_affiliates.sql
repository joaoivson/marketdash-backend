-- Migration 019: Programa de afiliados (Indique & Ganhe)
--
-- Captura `ref` no primeiro login do indicado e gera comissão a cada pagamento Cakto
-- aprovado. Pagamento aos afiliados é manual (admin marca como pago após PIX).
--
-- Após aplicar, definir admin manualmente:
--   UPDATE users SET is_admin = true WHERE email = 'relacionamento@marketdash.com.br';

-- 1. Estender users
ALTER TABLE users ADD COLUMN IF NOT EXISTS referrer_user_id INTEGER REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE users ADD COLUMN IF NOT EXISTS pix_key VARCHAR(255);
CREATE INDEX IF NOT EXISTS idx_users_referrer_user_id ON users(referrer_user_id);

-- 2. Tabela de comissões
CREATE TABLE IF NOT EXISTS commissions (
    id SERIAL PRIMARY KEY,
    referrer_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    referred_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    cakto_transaction_id VARCHAR(255),
    amount NUMERIC(10,2) NOT NULL,
    base_amount NUMERIC(10,2) NOT NULL,
    rate NUMERIC(5,4) NOT NULL DEFAULT 0.40,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    paid_at TIMESTAMP WITH TIME ZONE,
    payment_reference VARCHAR(255),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- Idempotência: o mesmo evento Cakto não pode gerar duas comissões
CREATE UNIQUE INDEX IF NOT EXISTS idx_commissions_cakto_tx
  ON commissions(cakto_transaction_id)
  WHERE cakto_transaction_id IS NOT NULL;

-- Listagens frequentes
CREATE INDEX IF NOT EXISTS idx_commissions_referrer_status ON commissions(referrer_user_id, status);
CREATE INDEX IF NOT EXISTS idx_commissions_referred ON commissions(referred_user_id);
