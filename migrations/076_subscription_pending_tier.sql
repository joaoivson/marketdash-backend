-- 076: Assinatura — compra pendente de tier menor (spec §10.2).
--
-- ⚠️ PROTOCOLO (mesmo da 058/067/074): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa o model — `create_all` NÃO adiciona coluna em tabela que
-- já existe (o boot-ALTER em db/base.py é a rede extra).
--
-- Bug real: `subscriptions` tem uma linha por usuário (user_id UNIQUE,
-- last-write-wins). Comprar Pro com MAX ainda vigente sobrescrevia a linha e
-- REBAIXAVA na hora — perda de acesso pago. Agora a ativação de tier menor
-- (com a vigente ainda com acesso e txn diferente) fica nas colunas
-- pending_* e só é promovida quando a principal perder o acesso
-- (SubscriptionService, sem depender de webhook novo).
--
-- RLS: `subscriptions` já tem política por usuário — coluna nova em tabela
-- existente herda a policy.

BEGIN;

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS pending_plan VARCHAR;

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS pending_periodo VARCHAR(32);

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS pending_vence_em TIMESTAMPTZ;

ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS pending_provider_transaction_id VARCHAR;

COMMIT;
