-- Migration 052: Automação Instagram — comentário → direct
--
-- Conexão via **Business Login for Instagram** (host graph.instagram.com), com
-- credenciais próprias (INSTAGRAM_APP_ID/SECRET) dentro do mesmo app da Meta.
-- Independente da conexão de anúncios (Facebook Login for Business): dois tokens,
-- dois ciclos de renovação, dois hosts. Se uma permissão do Instagram for
-- revogada, o Meta Ads continua funcionando.
--
-- RLS por app.current_user_id, no mesmo padrão das migrations 014/021.

-- ──────────────────────────────────────────────────────────────────────────
-- instagram_connections: 1 conta profissional conectada por usuário
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instagram_connections (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    ig_user_id VARCHAR(64) NOT NULL,
    ig_username VARCHAR(255) NULL,
    ig_avatar_url TEXT NULL,
    -- Instagram User access token (longo, 60 dias), criptografado com a MESMA
    -- chave Fernet do Shopee/Facebook (SHOPEE_ENCRYPTION_KEY).
    access_token TEXT NOT NULL,
    token_expires_at TIMESTAMPTZ NULL,
    -- Escopos concedidos (csv) — usado pra detectar login antigo sem messaging.
    scopes TEXT NULL,
    -- ativo | expirado | revogado
    status VARCHAR(16) NOT NULL DEFAULT 'ativo',
    -- Assinar o campo `comments` no painel vale só para o APP. Cada CONTA precisa
    -- ser inscrita por API (POST /{ig-user-id}/subscribed_apps). Sem isso o webhook
    -- nunca dispara e não há erro em lugar nenhum — daí guardarmos o estado aqui,
    -- para a tela poder avisar em vez de a aluna descobrir pelo silêncio.
    webhook_subscrito BOOLEAN NOT NULL DEFAULT FALSE,
    webhook_subscrito_em TIMESTAMPTZ NULL,
    webhook_erro TEXT NULL,
    connected_at TIMESTAMPTZ DEFAULT NOW(),
    last_refreshed_at TIMESTAMPTZ NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_instagram_connection_user UNIQUE(user_id)
);

-- O webhook chega identificado pelo ig_user_id, não pelo user_id do MarketDash:
-- este índice é o caminho quente de TODA notificação de comentário.
CREATE UNIQUE INDEX IF NOT EXISTS uq_instagram_connections_ig_user
    ON instagram_connections(ig_user_id);
CREATE INDEX IF NOT EXISTS idx_instagram_connections_user ON instagram_connections(user_id);
-- Alimenta o cron de renovação (tokens vencendo em < 10 dias).
CREATE INDEX IF NOT EXISTS idx_instagram_connections_expiry
    ON instagram_connections(token_expires_at) WHERE status = 'ativo';

ALTER TABLE instagram_connections ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS instagram_connections_iso ON instagram_connections;
CREATE POLICY instagram_connections_iso ON instagram_connections
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- ──────────────────────────────────────────────────────────────────────────
-- instagram_automations
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instagram_automations (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connection_id INTEGER NOT NULL REFERENCES instagram_connections(id) ON DELETE CASCADE,
    nome VARCHAR(255) NOT NULL,

    -- post_especifico | qualquer | proximo
    escopo VARCHAR(24) NOT NULL DEFAULT 'post_especifico',
    -- NULL em 'qualquer'; em 'proximo' fica NULL até o próximo post ser capturado
    media_id VARCHAR(64) NULL,
    media_thumbnail_url TEXT NULL,
    media_caption_preview TEXT NULL,
    media_permalink TEXT NULL,

    -- palavras | qualquer
    trigger_tipo VARCHAR(16) NOT NULL DEFAULT 'palavras',
    -- Guardadas JÁ NORMALIZADAS (minúsculas, sem acento/emoji) para o matching
    -- não pagar normalização a cada comentário. O texto original fica em
    -- palavras_exibicao, que é o que a tela mostra.
    palavras JSONB NOT NULL DEFAULT '[]'::jsonb,
    palavras_exibicao JSONB NOT NULL DEFAULT '[]'::jsonb,

    resposta_publica_ativa BOOLEAN NOT NULL DEFAULT TRUE,
    resposta_publica_variacoes JSONB NOT NULL DEFAULT '[]'::jsonb,
    -- Índice da próxima variação a usar — a rotação precisa sobreviver a
    -- reinício de worker, então mora no banco, não em memória.
    resposta_publica_indice INTEGER NOT NULL DEFAULT 0,

    dm_texto TEXT NOT NULL DEFAULT '',

    -- ativa | pausada | rascunho
    status VARCHAR(16) NOT NULL DEFAULT 'rascunho',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_instagram_automations_user ON instagram_automations(user_id);
-- Caminho quente do webhook: "quais automações ativas cobrem este media_id".
CREATE INDEX IF NOT EXISTS idx_instagram_automations_conn_status
    ON instagram_automations(connection_id, status);
CREATE INDEX IF NOT EXISTS idx_instagram_automations_media
    ON instagram_automations(connection_id, media_id) WHERE media_id IS NOT NULL;

ALTER TABLE instagram_automations ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS instagram_automations_iso ON instagram_automations;
CREATE POLICY instagram_automations_iso ON instagram_automations
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- ──────────────────────────────────────────────────────────────────────────
-- instagram_events: um registro por comentário processado
-- ──────────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS instagram_events (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    automation_id INTEGER NULL REFERENCES instagram_automations(id) ON DELETE CASCADE,

    -- UNIQUE é a garantia ESTRUTURAL contra envio duplicado: mesmo que a Meta
    -- reentregue o webhook ou a fila processe a mensagem duas vezes, o segundo
    -- INSERT falha no banco. Não depender só da checagem em Python.
    comment_id VARCHAR(64) NOT NULL,
    media_id VARCHAR(64) NULL,
    commenter_id VARCHAR(64) NULL,
    commenter_username VARCHAR(255) NULL,
    comment_text TEXT NULL,
    comment_timestamp TIMESTAMPTZ NULL,

    -- enviado | falhou | expirado | duplicado | ignorado | sem_match | processando
    -- 'processando' é reserva transitória: a linha entra ANTES da chamada à Meta
    -- para que o UNIQUE de comment_id trave um segundo worker. Vira enviado ou
    -- falhou em seguida; se ficar preso assim, o worker caiu no meio do envio.
    dm_status VARCHAR(16) NOT NULL DEFAULT 'enviado',
    dm_message_id VARCHAR(128) NULL,
    -- nao_aplicavel | enviado | falhou | pulado
    reply_status VARCHAR(16) NULL,
    erro_codigo VARCHAR(32) NULL,
    erro_mensagem TEXT NULL,
    processed_at TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    CONSTRAINT uq_instagram_event_comment UNIQUE(comment_id)
);

-- Dedupe por pessoa: "essa pessoa já recebeu direct desta automação neste post?"
CREATE INDEX IF NOT EXISTS idx_instagram_events_dedupe
    ON instagram_events(automation_id, media_id, commenter_id);
CREATE INDEX IF NOT EXISTS idx_instagram_events_user_processed
    ON instagram_events(user_id, processed_at DESC);
-- Alimenta o throttle horário (quantos directs saíram na última hora).
CREATE INDEX IF NOT EXISTS idx_instagram_events_user_enviado
    ON instagram_events(user_id, processed_at) WHERE dm_status = 'enviado';

ALTER TABLE instagram_events ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS instagram_events_iso ON instagram_events;
CREATE POLICY instagram_events_iso ON instagram_events
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);
