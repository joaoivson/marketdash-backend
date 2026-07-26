-- Migration 038: charges_completed em subscription_events
--
-- A Kiwify manda mais de um webhook (order_approved, subscription_renewed) pra
-- MESMA cobrança — cada um com o mesmo Commissions.charge_amount. Somar por evento
-- dobra o faturamento. O payload de cada evento traz em
-- Subscription.charges.completed (array cumulativo) a lista de cobranças REAIS já
-- pagas daquela assinatura, cada uma com seu próprio order_id — essa é a fonte
-- de verdade pra "cobrança distinta". Guardamos o array pra permitir reconciliação
-- futura (ex.: cobrança que existiu na Kiwify mas nunca gerou webhook aqui).
--
-- Aditiva: coluna nova, nullable, não quebra nada existente.

ALTER TABLE subscription_events
  ADD COLUMN IF NOT EXISTS charges_completed JSONB;
