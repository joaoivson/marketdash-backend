-- Migration 042: cancelado-com-acesso da era pré-subscription_events
--
-- Complementa a 041. Aquela deriva o acesso do histórico em subscription_events,
-- mas essa tabela só existe desde ~25/07/2026 (9 linhas na época deste fix). Quem
-- cancelou ANTES disso não tem evento e ficou de fora — continuava bloqueado mesmo
-- com o período pago em aberto (ex.: assinatura trimestral cancelada em 10/07 com
-- acesso até 10/10).
--
-- Aqui a fonte de verdade é a própria linha de subscriptions.
--
-- Conservador de propósito: exige prova POSITIVA de pagamento
-- (provider_payment_status IN ('paid','approved')). Reembolso, chargeback e
-- pagamento recusado continuam sem acesso — o corte imediato lá está correto.
--
-- Idempotente: só toca em quem está inativo com janela ainda válida.

BEGIN;

WITH com_periodo_pago AS (
  SELECT
    s.user_id,
    COALESCE(s.provider_due_date, s.expires_at, s.cakto_due_date) AS acesso_ate
  FROM subscriptions s
  WHERE s.is_active IS NOT TRUE
    AND COALESCE(s.provider_due_date, s.expires_at, s.cakto_due_date) > NOW()
    AND LOWER(COALESCE(s.provider_payment_status, '')) IN ('paid', 'approved')
    AND LOWER(COALESCE(s.provider_subscription_status, s.assinatura_status, '')) IN
        ('canceled', 'cancelled', 'cancelada', 'cancelado')
)
UPDATE subscriptions s
SET
  is_active = TRUE,
  -- status ficava 'ativa' obsoleto: check_and_update_subscription derruba is_active
  -- pela due_date mas nunca atualizava o assinatura_status
  assinatura_status = 'cancelada',
  assinatura_vence_em = cpp.acesso_ate,
  expires_at = cpp.acesso_ate,
  last_validation_at = NOW()
FROM com_periodo_pago cpp
WHERE s.user_id = cpp.user_id;

COMMIT;

-- Conferência: ninguém deve sobrar inativo com janela paga em aberto
-- SELECT u.email, s.is_active, s.assinatura_status, s.provider_payment_status,
--        COALESCE(s.provider_due_date, s.expires_at) AS acesso_ate
-- FROM subscriptions s JOIN users u ON u.id = s.user_id
-- WHERE s.is_active IS NOT TRUE
--   AND COALESCE(s.provider_due_date, s.expires_at, s.cakto_due_date) > NOW();
