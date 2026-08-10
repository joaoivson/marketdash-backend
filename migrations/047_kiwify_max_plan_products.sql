-- Migration 047: oferta MAX (mensal/trimestral/anual) — mapeamento Kiwify → plano
--
-- MAX ainda não aparece na página de vendas; os checkout links já existem (3
-- ofertas criadas na Kiwify). Isso só garante que o webhook, ao receber um
-- pagamento nesses links, identifique o tier corretamente e libere o plano.

INSERT INTO kiwify_plan_products (product_id, plano, periodo, checkout_url, label)
VALUES
  ('rTfikTj', 'max', 'mensal', 'https://pay.kiwify.com.br/rTfikTj', 'Max Mensal'),
  ('HPql4oU', 'max', 'trimestral', 'https://pay.kiwify.com.br/HPql4oU', 'Max Trimestral'),
  ('5l1Sdau', 'max', 'anual', 'https://pay.kiwify.com.br/5l1Sdau', 'Max Anual')
ON CONFLICT (product_id) DO NOTHING;
