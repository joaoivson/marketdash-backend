-- Migration 048: canceled_at e cancel_reason em subscription_events
--
-- Suporta o import histórico Kiwify (43 assinaturas, abr-ago/2026) e casos
-- futuros de cancelamento pelo produtor. canceled_at é o instante do
-- cancelamento em si (distinto de access_until, que é até quando o acesso
-- permanece válido); cancel_reason é o motivo textual da Kiwify — usado por
-- churn_for_month() pra excluir ajustes do produtor da métrica de churn
-- (ver PRODUTOR_ADJUSTMENT_REASONS em admin_metrics_service.py).
--
-- Aditiva: colunas novas, nullable, não quebra nada existente.

ALTER TABLE subscription_events
  ADD COLUMN IF NOT EXISTS canceled_at TIMESTAMPTZ,
  ADD COLUMN IF NOT EXISTS cancel_reason TEXT;
