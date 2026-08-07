-- 045: resumo diário no WhatsApp — opt-in e log de envio.
--
-- Ver docs/superpowers/specs/2026-08-07-resumo-whatsapp-design.md.

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_optins (
    id              BIGSERIAL PRIMARY KEY,
    -- Uma linha por afiliada: ela tem um número, não vários.
    user_id         INTEGER NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    numero          TEXT NOT NULL,          -- E.164 sem '+': 5511999998888
    status          TEXT NOT NULL DEFAULT 'pendente',  -- pendente|confirmado|desligado
    codigo          TEXT,
    codigo_expira_em TIMESTAMPTZ,
    tentativas      INTEGER NOT NULL DEFAULT 0,
    confirmado_em   TIMESTAMPTZ,
    desligado_em    TIMESTAMPTZ,
    -- Preenchido quando a afiliada responde SAIR no próprio WhatsApp.
    desligado_por   TEXT,                   -- app|whatsapp|falha
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- O webhook da Evolution chega com o número, não com o user_id: é por aqui que
-- o SAIR encontra de quem é a linha.
CREATE INDEX IF NOT EXISTS ix_whatsapp_optins_numero ON whatsapp_optins (numero);
CREATE INDEX IF NOT EXISTS ix_whatsapp_optins_status ON whatsapp_optins (status);

CREATE TABLE IF NOT EXISTS whatsapp_envios (
    id          BIGSERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    tipo        TEXT NOT NULL,              -- resumo_diario|confirmacao
    referencia  DATE,                       -- dia coberto pelo resumo
    status      TEXT NOT NULL,              -- enviado|falhou
    erro        TEXT,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Idempotência do lote diário: se o cron rodar duas vezes (retry do pg_net,
-- rerun manual), a afiliada não recebe a mesma mensagem de novo. Parcial em
-- status='enviado' de propósito — tentativa que falhou pode ser refeita.
CREATE UNIQUE INDEX IF NOT EXISTS ux_whatsapp_envio_do_dia
    ON whatsapp_envios (user_id, tipo, referencia)
 WHERE referencia IS NOT NULL AND status = 'enviado';

CREATE INDEX IF NOT EXISTS ix_whatsapp_envios_criado ON whatsapp_envios (criado_em DESC);

COMMIT;
