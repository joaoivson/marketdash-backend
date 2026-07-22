-- Migration 034: planos Essencial/Pro (+ Max futuro) e flag is_demo
--
-- Extende subscriptions com período/status tipados e users.is_demo.
-- Migra assinantes ativos (e quem já tem captura/links) para pro.

ALTER TABLE subscriptions
  ADD COLUMN IF NOT EXISTS plano_periodo VARCHAR(32) NULL,
  ADD COLUMN IF NOT EXISTS assinatura_status VARCHAR(32) NULL,
  ADD COLUMN IF NOT EXISTS assinatura_vence_em TIMESTAMPTZ NULL;

ALTER TABLE users
  ADD COLUMN IF NOT EXISTS is_demo BOOLEAN NOT NULL DEFAULT FALSE;

-- Tabela de mapeamento Kiwify product_id → (plano, periodo)
CREATE TABLE IF NOT EXISTS kiwify_plan_products (
  id SERIAL PRIMARY KEY,
  product_id VARCHAR(128) NOT NULL UNIQUE,
  plano VARCHAR(32) NOT NULL,
  periodo VARCHAR(32) NOT NULL,
  checkout_url TEXT NULL,
  label VARCHAR(255) NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Seed pelos checkout links conhecidos (product_id = slug do checkout até IDs reais)
INSERT INTO kiwify_plan_products (product_id, plano, periodo, checkout_url, label)
VALUES
  ('uMRfGkI', 'essencial', 'mensal', 'https://pay.kiwify.com.br/uMRfGkI', 'Essencial Mensal'),
  ('vkKX959', 'essencial', 'trimestral', 'https://pay.kiwify.com.br/vkKX959', 'Essencial Trimestral'),
  ('EZ81jlu', 'essencial', 'anual', 'https://pay.kiwify.com.br/EZ81jlu', 'Essencial Anual'),
  ('u12boOS', 'pro', 'mensal', 'https://pay.kiwify.com.br/u12boOS', 'Pro Mensal'),
  ('9B9lXa6', 'pro', 'trimestral', 'https://pay.kiwify.com.br/9B9lXa6', 'Pro Trimestral'),
  ('4lhuudg', 'pro', 'anual', 'https://pay.kiwify.com.br/4lhuudg', 'Pro Anual')
ON CONFLICT (product_id) DO NOTHING;

-- Ativos → pro
UPDATE subscriptions
SET plan = 'pro',
    assinatura_status = 'ativa',
    plano_periodo = COALESCE(plano_periodo, 'mensal')
WHERE is_active = TRUE;

-- Quem já tem captura ou links → pro (mesmo se inativo no status)
UPDATE subscriptions s
SET plan = 'pro',
    assinatura_status = COALESCE(assinatura_status, 'ativa')
WHERE s.user_id IN (
  SELECT DISTINCT user_id FROM capture_sites
  UNION
  SELECT DISTINCT user_id FROM custom_links
);

-- free/legado sem assinatura → essencial
UPDATE subscriptions
SET plan = 'essencial',
    assinatura_status = COALESCE(assinatura_status, 'cancelada')
WHERE LOWER(COALESCE(plan, 'free')) IN ('free', 'gratis', 'gratuito', 'essencial')
  AND is_active = FALSE;
