-- Migration 041: restaura acesso de quem cancelou mas ainda tem período pago
--
-- Bug: o webhook `subscription_canceled` da Kiwify vem com
-- `customer_access.has_access = true` e `access_until` no fim do período já pago,
-- mas o handler zerava `subscriptions.is_active` na hora. Resultado: o painel admin
-- (que lê subscription_events) mostrava "Cancelado c/ acesso" enquanto o app barrava
-- o usuário — ele pagou o mês e não conseguia entrar.
--
-- Bug 2 (achado no diagnóstico): subscription_events é gravado ANTES de criar/achar
-- o usuário, então na primeira compra o evento nasce com user_id NULL. O código
-- passou a religar isso no próprio webhook; aqui consertamos o histórico.
--
-- Idempotente: só toca em quem está inativo com janela de acesso ainda válida.

BEGIN;

-- 1) Religa eventos órfãos ao usuário pelo e-mail
UPDATE subscription_events se
SET user_id = u.id
FROM users u
WHERE se.user_id IS NULL
  AND se.customer_email IS NOT NULL
  AND LOWER(u.email) = LOWER(se.customer_email);

-- 2) Devolve o acesso de quem cancelou dentro do período pago.
--    Último evento por usuário (subscription_events é append-only).
WITH ultimo_evento AS (
  SELECT DISTINCT ON (se.user_id)
    se.user_id,
    se.event_type,
    se.has_access,
    se.access_until,
    se.next_payment
  FROM subscription_events se
  WHERE se.user_id IS NOT NULL
  ORDER BY se.user_id, se.received_at DESC, se.id DESC
),
com_acesso AS (
  SELECT
    ue.user_id,
    COALESCE(ue.access_until, ue.next_payment) AS acesso_ate
  FROM ultimo_evento ue
  WHERE ue.event_type = 'subscription_canceled'
    -- Reembolso/chargeback NÃO entram: lá o corte imediato está correto
    AND ue.has_access IS DISTINCT FROM FALSE
    AND COALESCE(ue.access_until, ue.next_payment) > NOW()
)
UPDATE subscriptions s
SET
  is_active = TRUE,
  assinatura_status = 'cancelada',
  assinatura_vence_em = ca.acesso_ate,
  expires_at = ca.acesso_ate,
  last_validation_at = NOW()
FROM com_acesso ca
WHERE s.user_id = ca.user_id
  AND s.is_active = FALSE;

COMMIT;

-- Conferência (rodar depois): quem ficou cancelado-com-acesso
-- SELECT u.email, s.is_active, s.assinatura_status, s.assinatura_vence_em
-- FROM subscriptions s
-- JOIN users u ON u.id = s.user_id
-- WHERE s.assinatura_status = 'cancelada' AND s.is_active = TRUE
-- ORDER BY s.assinatura_vence_em;
