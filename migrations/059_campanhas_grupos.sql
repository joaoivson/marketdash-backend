-- 059: Módulo de Grupos — F2: Campanhas (conjuntos de grupos com link de
-- entrada, roteiros e métricas próprias — spec Luiz v1.1 §4.4).
--
-- ⚠️ PROTOCOLO (revisado 25/08): aplicar em HML ANTES do deploy/model (em dev
-- local contra hml, ANTES de escrever o model — o --reload é deploy
-- instantâneo). Em PRODUÇÃO: somente na promoção develop→main, imediatamente
-- antes do merge.
--
-- Decisões que a estrutura carrega:
--  * grupo pode estar em N campanhas (PK composta em campanha_grupos);
--  * `posicao` é a ordem de preenchimento do rotativo (arrastável na UI);
--  * `aberto` controla se o grupo recebe entrada pelo link (F6);
--  * prefixo/sufixo e modo_imagem são configuração POR CAMPANHA (spec §10.4:
--    nunca perguntar a cada envio).

BEGIN;

CREATE TABLE IF NOT EXISTS campanhas (
    id                    SERIAL PRIMARY KEY,
    user_id               INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nome                  VARCHAR(120) NOT NULL,
    descricao             TEXT,
    status                VARCHAR(16) NOT NULL DEFAULT 'ativa',       -- ativa|pausada|arquivada
    estrategia_entrada    VARCHAR(16) NOT NULL DEFAULT 'sequencial',  -- sequencial|aleatoria
    abertura_automatica   BOOLEAN NOT NULL DEFAULT TRUE,
    reabertura_automatica BOOLEAN NOT NULL DEFAULT TRUE,
    prefixo               TEXT,
    sufixo                TEXT,
    modo_imagem           VARCHAR(16) NOT NULL DEFAULT 'link_preview', -- link_preview|imagem_normal
    criado_em             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_campanhas_user ON campanhas (user_id);

CREATE TABLE IF NOT EXISTS campanha_grupos (
    campanha_id   INTEGER NOT NULL REFERENCES campanhas(id) ON DELETE CASCADE,
    grupo_id      INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    posicao       INTEGER NOT NULL DEFAULT 0,
    aberto        BOOLEAN NOT NULL DEFAULT TRUE,
    adicionado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campanha_id, grupo_id)
);
-- O rotativo do link de entrada (F6) varre por grupo_id nas duas direções.
CREATE INDEX IF NOT EXISTS ix_campanha_grupos_grupo ON campanha_grupos (grupo_id);

ALTER TABLE campanhas ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS campanhas_iso ON campanhas;
CREATE POLICY campanhas_iso ON campanhas
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE campanha_grupos ENABLE ROW LEVEL SECURITY;

COMMIT;
