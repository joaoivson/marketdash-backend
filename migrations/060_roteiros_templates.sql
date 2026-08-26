-- 060: Módulo de Grupos — F3: roteiros, motor de envio, templates e blacklist.
--
-- ⚠️ PROTOCOLO: hml ANTES do deploy/model; produção SOMENTE na promoção
-- develop→main, imediatamente antes do merge.
--
-- Decisões que a estrutura carrega (spec §4.8-4.10 + plano §motor):
--  * roteiro = sequência de passos com âncora + offsets relativos; envio
--    rápido é um roteiro de 1 passo (origem='envio_rapido') — MESMO motor;
--  * tipo_conteudo POLIMÓRFICO desde o início (texto|midia|oferta|acao_grupo);
--  * roteiro_mensagens é pré-materializada (uma linha por passo×grupo, com
--    agendado_para absoluto) — o claim atômico trabalha sobre ela;
--  * linha presa em 'enviando' vira 'falhou' e NUNCA é reenviada;
--  * contagem de teto usa o índice parcial (user_id, enviado_em) — janela
--    (inicio,fim) BRT calculada em Python, NUNCA func.date();
--  * variação de template é SORTEADA por peso (anti-ban); IA só gera variação
--    na tela de templates (F4).

BEGIN;

CREATE TABLE IF NOT EXISTS templates_mensagem (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nome          VARCHAR(120) NOT NULL,
    tipo          VARCHAR(12) NOT NULL DEFAULT 'oferta',   -- oferta|livre
    ativo         BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_templates_mensagem_user ON templates_mensagem (user_id);

CREATE TABLE IF NOT EXISTS template_variacoes (
    id          SERIAL PRIMARY KEY,
    template_id INTEGER NOT NULL REFERENCES templates_mensagem(id) ON DELETE CASCADE,
    corpo       TEXT NOT NULL,   -- placeholders {produto},{preco_de},{preco_por},{desconto},{loja},{link},{cupom}
    peso        INTEGER NOT NULL DEFAULT 1,
    ativa       BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS ix_template_variacoes_template ON template_variacoes (template_id);

CREATE TABLE IF NOT EXISTS roteiros (
    id            SERIAL PRIMARY KEY,
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    campanha_id   INTEGER REFERENCES campanhas(id) ON DELETE CASCADE,
    nome          VARCHAR(120) NOT NULL,
    status        VARCHAR(12) NOT NULL DEFAULT 'rascunho',   -- rascunho|pronto
    origem        VARCHAR(16) NOT NULL DEFAULT 'editor',     -- editor|envio_rapido
    criado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_roteiros_user ON roteiros (user_id);
CREATE INDEX IF NOT EXISTS ix_roteiros_campanha ON roteiros (campanha_id);

CREATE TABLE IF NOT EXISTS roteiro_passos (
    id             SERIAL PRIMARY KEY,
    roteiro_id     INTEGER NOT NULL REFERENCES roteiros(id) ON DELETE CASCADE,
    ordem          INTEGER NOT NULL,
    tipo_tempo     VARCHAR(12) NOT NULL DEFAULT 'ancora',   -- ancora|relativo
    hora_fixa      TIME,
    data_fixa      DATE,
    offset_minutos INTEGER,
    tipo_conteudo  VARCHAR(16) NOT NULL,   -- texto|midia|oferta|acao_grupo
    texto          TEXT,
    midia_url      TEXT,
    oferta_url     TEXT,
    template_id    INTEGER REFERENCES templates_mensagem(id) ON DELETE SET NULL,
    acao           VARCHAR(24),            -- renomear_grupo|abrir_entrada|fechar_entrada
    acao_parametro TEXT,
    grupos_alvo    VARCHAR(12) NOT NULL DEFAULT 'todos',    -- todos|selecao
    grupos_alvo_ids JSONB,
    marcar_todos   VARCHAR(8) NOT NULL DEFAULT 'nunca',     -- nunca|sempre
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_roteiro_passos_roteiro ON roteiro_passos (roteiro_id, ordem);

CREATE TABLE IF NOT EXISTS roteiro_execucoes (
    id                  SERIAL PRIMARY KEY,
    roteiro_id          INTEGER NOT NULL REFERENCES roteiros(id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    data_ancora         DATE NOT NULL,
    status              VARCHAR(12) NOT NULL DEFAULT 'agendada',
                        -- agendada|enviando|pausada|concluida|cancelada|falhou
    proxima_execucao_em TIMESTAMPTZ,
    total    INTEGER NOT NULL DEFAULT 0,
    enviados INTEGER NOT NULL DEFAULT 0,
    erros    INTEGER NOT NULL DEFAULT 0,
    pulados  INTEGER NOT NULL DEFAULT 0,
    iniciado_em  TIMESTAMPTZ,
    concluido_em TIMESTAMPTZ,
    criado_em    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- O tick do pg_cron varre SÓ este índice parcial — normalmente 0 linhas.
CREATE INDEX IF NOT EXISTS ix_roteiro_execucoes_tick
    ON roteiro_execucoes (proxima_execucao_em) WHERE status = 'agendada';
CREATE INDEX IF NOT EXISTS ix_roteiro_execucoes_user ON roteiro_execucoes (user_id);

CREATE TABLE IF NOT EXISTS roteiro_mensagens (
    id            BIGSERIAL PRIMARY KEY,
    execucao_id   INTEGER NOT NULL REFERENCES roteiro_execucoes(id) ON DELETE CASCADE,
    passo_id      INTEGER NOT NULL REFERENCES roteiro_passos(id) ON DELETE CASCADE,
    grupo_id      INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    instancia_id  INTEGER REFERENCES whatsapp_instancias(id) ON DELETE SET NULL,  -- resolvida no disparo
    user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    agendado_para TIMESTAMPTZ NOT NULL,
    status        VARCHAR(12) NOT NULL DEFAULT 'pendente',
                  -- pendente|enviando|enviado|falhou|pulado
    short_link    TEXT,      -- congelado no disparo
    texto_final   TEXT,      -- variação sorteada + link resolvido, congelado
    erro_motivo   TEXT,
    enviado_em    TIMESTAMPTZ,
    CONSTRAINT uq_roteiro_msg UNIQUE (execucao_id, passo_id, grupo_id)
);
-- Teto diário sem func.date(): janela BRT em Python sobre este parcial.
CREATE INDEX IF NOT EXISTS ix_roteiro_msg_teto
    ON roteiro_mensagens (user_id, enviado_em) WHERE status = 'enviado';
-- Caminho do claim atômico.
CREATE INDEX IF NOT EXISTS ix_roteiro_msg_claim
    ON roteiro_mensagens (execucao_id, status, agendado_para);

CREATE TABLE IF NOT EXISTS blacklist_numeros (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    numero_hash VARCHAR(64) NOT NULL,
    motivo      TEXT,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_blacklist UNIQUE (user_id, numero_hash)
);

-- Janela de envio por usuária (D4): JSONB em user_settings, shape validado
-- por Pydantic no service; NULL = default 08:00-22:00 todos os dias.
ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS whatsapp_envio_config JSONB;

-- RLS (padrão 052/058): policy user_id onde há user_id; enable-sem-policy
-- nas tabelas acessadas só via join do backend.
ALTER TABLE templates_mensagem ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS templates_mensagem_iso ON templates_mensagem;
CREATE POLICY templates_mensagem_iso ON templates_mensagem
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE template_variacoes ENABLE ROW LEVEL SECURITY;

ALTER TABLE roteiros ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS roteiros_iso ON roteiros;
CREATE POLICY roteiros_iso ON roteiros
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE roteiro_passos ENABLE ROW LEVEL SECURITY;

ALTER TABLE roteiro_execucoes ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS roteiro_execucoes_iso ON roteiro_execucoes;
CREATE POLICY roteiro_execucoes_iso ON roteiro_execucoes
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE roteiro_mensagens ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS roteiro_mensagens_iso ON roteiro_mensagens;
CREATE POLICY roteiro_mensagens_iso ON roteiro_mensagens
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

ALTER TABLE blacklist_numeros ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS blacklist_numeros_iso ON blacklist_numeros;
CREATE POLICY blacklist_numeros_iso ON blacklist_numeros
    FOR ALL USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

COMMIT;
