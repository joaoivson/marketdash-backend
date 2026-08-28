-- 068: Proxy por sessão (anti-banimento) — pool de IPs + vínculo com a sessão.
--
-- ⚠️ PROTOCOLO (mesmo da 058/067): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa o model. O `create_all` do boot cria qualquer tabela nova
-- SEM RLS — e esta guarda CREDENCIAL de proxy. "Migration não aplicada" nunca
-- significa "tabela não existe".
--
-- A decisão de produto que o schema carrega (plano 27/08):
--
--  * proxy é STICKY, não rotativo. O que derruba número no WhatsApp é TROCAR
--    de IP, não repetir o mesmo. `proxy_trocas`/`proxy_fixado_em` existem para
--    tornar a troca um evento raro, medido e com cooldown — nunca rotina.
--  * AFINIDADE POR USUÁRIA: os chips da mesma afiliada compartilham IP (três
--    aparelhos na mesma casa é retrato coerente); chips de usuárias diferentes
--    NÃO — um banimento contaminaria a vizinhança. Isso é regra de serviço
--    (proxy_pool_service), não do schema: o Postgres não tem como expressá-la
--    sem desnormalizar o user_id para cá.
--  * `max_sessoes` conta apenas instâncias ATIVAS (status <> 'removida').
--
-- RLS: a tabela é GLOBAL e administrativa — não tem `user_id`, então não há
-- política por usuária a escrever. `ENABLE ROW LEVEL SECURITY` sem policy
-- nenhuma é o estado mais restritivo possível (nega tudo para quem não é dono
-- da tabela). Credencial de proxy não deve ser legível por role de aplicação
-- que não seja a nossa.

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_proxies (
    id              SERIAL PRIMARY KEY,
    rotulo          VARCHAR(80)  NOT NULL,       -- "BR-móvel-01" (aparece no admin)
    tipo            VARCHAR(16)  NOT NULL,       -- residencial | movel | datacenter
    host            VARCHAR(255) NOT NULL,
    porta           INTEGER      NOT NULL,
    -- Fernet (app/core/encryption.py), mesma chave dos tokens da Shopee. Nunca
    -- sai em claro: nem em log, nem em resposta de API, nem em mensagem de erro.
    usuario_cifrado TEXT,
    senha_cifrada   TEXT,
    pais            VARCHAR(2)   NOT NULL DEFAULT 'BR',
    max_sessoes     INTEGER      NOT NULL DEFAULT 3,
    ativo           BOOLEAN      NOT NULL DEFAULT TRUE,
    status          VARCHAR(16)  NOT NULL DEFAULT 'ok',   -- ok | degradado | quarentena
    falhas_seguidas INTEGER      NOT NULL DEFAULT 0,      -- sonda de saúde (2 → degradado, 4 → quarentena)
    ultimo_erro     TEXT,
    ultimo_ip       VARCHAR(64),                 -- o que a sonda viu pela última vez
    ultimo_pais     VARCHAR(8),                  -- alerta quando sai de BR
    verificado_em   TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE whatsapp_instancias
    ADD COLUMN IF NOT EXISTS proxy_id        INTEGER REFERENCES whatsapp_proxies(id) ON DELETE SET NULL;
ALTER TABLE whatsapp_instancias
    ADD COLUMN IF NOT EXISTS proxy_fixado_em TIMESTAMPTZ;
ALTER TABLE whatsapp_instancias
    ADD COLUMN IF NOT EXISTS proxy_trocas    INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_whatsapp_instancias_proxy
    ON whatsapp_instancias (proxy_id);

ALTER TABLE whatsapp_proxies ENABLE ROW LEVEL SECURITY;

COMMIT;
