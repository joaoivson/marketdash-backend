-- Migration 054: estado da assinatura de webhook por conta.
--
-- As colunas já nascem na 052 — esta migration existe só para quem aplicou a 052
-- antes desta correção. É idempotente e não faz nada num banco já em dia.
--
-- Contexto: assinar o campo `comments` no App Dashboard vale para o APP. Cada
-- conta profissional conectada precisa de uma chamada própria
-- (POST /{ig-user-id}/subscribed_apps?subscribed_fields=comments). Sem ela o
-- webhook nunca dispara — e não há erro em canto nenhum: o OAuth funciona, a tela
-- funciona, a automação salva, e nada acontece. Guardar o estado permite avisar.

ALTER TABLE instagram_connections
    ADD COLUMN IF NOT EXISTS webhook_subscrito BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE instagram_connections
    ADD COLUMN IF NOT EXISTS webhook_subscrito_em TIMESTAMPTZ NULL;
ALTER TABLE instagram_connections
    ADD COLUMN IF NOT EXISTS webhook_erro TEXT NULL;
