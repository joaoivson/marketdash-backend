-- 062: Módulo de Grupos — F5: integrações de marketplace por usuária.
--
-- ⚠️ PROTOCOLO: hml ANTES do deploy/model; produção SOMENTE na promoção
-- develop→main, imediatamente antes do merge.
--
-- Substitui `shopee_integrations` (UNIQUE(user_id) = uma conta, um
-- marketplace) por um modelo que comporta N marketplaces e N contas por
-- marketplace (spec §4.1). SEM conceito de "principal": o sistema resolve a
-- integração pelo marketplace detectado na URL do produto; com 2+ ativas do
-- mesmo provedor, a afiliada escolhe pelo `label` na hora da conversão.
--
-- MIGRAÇÃO EM DOIS DEPLOYS (a tabela antiga NÃO é dropada — regra aditiva):
--   deploy A (esta fase): backfill abaixo + escrita nas DUAS tabelas;
--                         leitura continua em shopee_integrations.
--   deploy B (próximo ciclo): leitura passa para integracoes.
-- `credenciais` é TEXT com JSON cifrado por Fernet (SHOPEE_ENCRYPTION_KEY) —
-- a spec pedia "JSONB criptografado", mas cifrar o blob exige TEXT e nunca
-- precisamos consultar dentro da credencial.

BEGIN;

CREATE TABLE IF NOT EXISTS integracoes (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    provedor      VARCHAR(24) NOT NULL,          -- shopee|amazon|mercado_livre|magalu|...
    label         VARCHAR(64) NOT NULL DEFAULT 'principal',
    credenciais   TEXT NOT NULL,                 -- JSON cifrado (Fernet)
    ativa         BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_integracoes UNIQUE (user_id, provedor, label)
);
CREATE INDEX IF NOT EXISTS ix_integracoes_user_provedor
    ON integracoes (user_id, provedor) WHERE ativa;

ALTER TABLE integracoes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS integracoes_iso ON integracoes;
CREATE POLICY integracoes_iso ON integracoes
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- Backfill idempotente: cada credencial Shopee existente vira uma linha.
-- O JSON aqui NÃO é cifrado de novo — `encrypted_password` já está cifrado
-- com a mesma chave; o service sabe ler os dois formatos (ver
-- integracao_service._credenciais_de).
INSERT INTO integracoes (user_id, provedor, label, credenciais, ativa)
SELECT si.user_id, 'shopee', 'principal',
       json_build_object('app_id', si.app_id,
                         'encrypted_password', si.encrypted_password)::text,
       COALESCE(si.is_active, TRUE)
FROM shopee_integrations si
ON CONFLICT (user_id, provedor, label) DO NOTHING;

COMMIT;
