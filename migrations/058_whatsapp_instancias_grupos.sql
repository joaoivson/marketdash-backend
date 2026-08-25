-- 058: Módulo de Grupos — F1: números conectados (sessões WAHA) e grupos.
--
-- ⚠️ PROTOCOLO (plano 25/08, §create_all×RLS): aplicar em HML E PRODUÇÃO
-- ANTES do deploy que importa os models — o create_all do boot cria qualquer
-- tabela nova em produção SEM RLS se esta migration não chegar primeiro.
-- Conferir depois: pg_class.relrowsecurity = true nas três tabelas.
--
-- Decisões que esta estrutura carrega (spec Luiz v1.1 §4.2-4.3):
--  * user_id SEM unique em whatsapp_instancias — multi-número desde o v1;
--  * nome_instancia UNIQUE — é a chave de roteamento do webhook, formato
--    mkd{ref4-do-ambiente}u{user_id}x{hex4} (prefixo impede fratricídio
--    hml×prod no mesmo servidor WAHA);
--  * grupo NUNCA é deletado (ativo=false quando some do sync) — apagar
--    destruiria a atribuição histórica de comissão;
--  * sub_id nasce NO SYNC (não no primeiro envio): atribuição perdida no
--    intervalo seria irrecuperável. Formato wg{base36(id)} — satisfaz o
--    ^[A-Za-z0-9]+$ da Shopee e é estável para sempre;
--  * participantes é um AGREGADO (contagem) — a lista de membros do grupo
--    não toca o banco (LGPD).

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_instancias (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nome_exibicao   VARCHAR(120),
    nome_instancia  VARCHAR(64) NOT NULL,
    numero          VARCHAR(32),
    status          VARCHAR(16) NOT NULL DEFAULT 'criada',  -- criada|conectada|desconectada|removida
    teto_diario     INTEGER,                                -- NULL = default do sistema (env)
    falhas_seguidas INTEGER NOT NULL DEFAULT 0,             -- disjuntor por instância
    ultima_conexao_em TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_instancias_nome
    ON whatsapp_instancias (nome_instancia);
CREATE INDEX IF NOT EXISTS ix_whatsapp_instancias_user
    ON whatsapp_instancias (user_id);

CREATE TABLE IF NOT EXISTS whatsapp_grupos (
    id              SERIAL PRIMARY KEY,
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    jid             VARCHAR(64) NOT NULL,
    nome            VARCHAR(255),
    foto_url        TEXT,
    participantes   INTEGER NOT NULL DEFAULT 0,
    capacidade      INTEGER NOT NULL DEFAULT 1024,
    sou_admin       BOOLEAN NOT NULL DEFAULT FALSE,
    permite_envio   BOOLEAN NOT NULL DEFAULT FALSE,  -- sou_admin OR grupo aberto a todos
    link_convite    TEXT,
    categoria       VARCHAR(64),
    ativo           BOOLEAN NOT NULL DEFAULT TRUE,
    sub_id          VARCHAR(24),
    custom_link_id  INTEGER REFERENCES custom_links(id) ON DELETE SET NULL,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_whatsapp_grupos_user_jid UNIQUE (user_id, jid)
);
-- Parcial porque sub_id é preenchido logo após o INSERT (flush → wg{base36(id)}
-- → update, na mesma transação do sync) — nunca fica NULL em linha viva.
CREATE UNIQUE INDEX IF NOT EXISTS uq_whatsapp_grupos_sub_id
    ON whatsapp_grupos (sub_id) WHERE sub_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS whatsapp_grupo_instancias (
    grupo_id     INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    instancia_id INTEGER NOT NULL REFERENCES whatsapp_instancias(id) ON DELETE CASCADE,
    sou_admin    BOOLEAN NOT NULL DEFAULT FALSE,   -- por instância: o mesmo grupo pode ter 2 números
    PRIMARY KEY (grupo_id, instancia_id)
);
-- O PK (grupo_id, instancia_id) não serve consulta por instancia_id sozinho —
-- e desvincular/vincular do sync filtram exatamente por ele, a cada sync.
CREATE INDEX IF NOT EXISTS ix_wa_grupo_instancias_instancia
    ON whatsapp_grupo_instancias (instancia_id);
CREATE INDEX IF NOT EXISTS ix_whatsapp_grupos_user_ativo
    ON whatsapp_grupos (user_id, ativo);
CREATE INDEX IF NOT EXISTS ix_whatsapp_grupos_custom_link
    ON whatsapp_grupos (custom_link_id) WHERE custom_link_id IS NOT NULL;

-- RLS: policy por user_id nas tabelas que o têm (padrão da 052); a junção não
-- tem user_id → ENABLE sem policy (nega Data API/anon; o backend, dono das
-- tabelas, acessa por ownership — endurecido vs o precedente da 032).
ALTER TABLE whatsapp_instancias ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS whatsapp_instancias_iso ON whatsapp_instancias;
CREATE POLICY whatsapp_instancias_iso ON whatsapp_instancias
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE whatsapp_grupos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS whatsapp_grupos_iso ON whatsapp_grupos;
CREATE POLICY whatsapp_grupos_iso ON whatsapp_grupos
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE whatsapp_grupo_instancias ENABLE ROW LEVEL SECURITY;

COMMIT;
