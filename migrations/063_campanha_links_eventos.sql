-- 063: Módulo de Grupos — F6: link de entrada, eventos de grupo e snapshots.
--
-- ⚠️ PROTOCOLO: hml ANTES do deploy/model; produção SOMENTE na promoção
-- develop→main, imediatamente antes do merge.
--
-- Fecha a primeira metade da corrente (spec §1): anúncio → clique no link →
-- entrada no grupo. O `/g/{slug}` roteia a pessoa para um grupo com vaga e
-- registra o clique; o webhook de participantes registra a entrada; o par
-- (clique, entrada) é o que sustenta taxa de entrada, evasão e custo por
-- pessoa que FICOU.
--
-- LGPD: `identificador_hash` é sha256(jid + salt) — o número de quem entra
-- NUNCA toca o banco. É o suficiente para casar entrada com saída ("entraram
-- e ficaram") sem guardar dado pessoal de terceiro.

BEGIN;

CREATE TABLE IF NOT EXISTS campanha_links (
    id                SERIAL PRIMARY KEY,
    campanha_id       INTEGER NOT NULL REFERENCES campanhas(id) ON DELETE CASCADE,
    slug              VARCHAR(64) NOT NULL,
    titulo_previa     VARCHAR(160),
    descricao_previa  VARCHAR(300),
    banner_previa_url TEXT,
    pixel_facebook_id VARCHAR(32),
    pixel_eventos     JSONB NOT NULL DEFAULT '{"pageview": true, "lead": true}',
    ativo             BOOLEAN NOT NULL DEFAULT TRUE,
    criado_em         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_campanha_links_slug ON campanha_links (slug);
CREATE INDEX IF NOT EXISTS ix_campanha_links_campanha ON campanha_links (campanha_id);

CREATE TABLE IF NOT EXISTS campanha_link_eventos (
    id         BIGSERIAL PRIMARY KEY,
    link_id    INTEGER NOT NULL REFERENCES campanha_links(id) ON DELETE CASCADE,
    grupo_id   INTEGER REFERENCES whatsapp_grupos(id) ON DELETE SET NULL,
    ip_hash    VARCHAR(64),
    user_agent TEXT,
    referer    TEXT,
    -- /g/preview/{slug}: redireciona igual, mas NUNCA entra em métrica.
    is_teste   BOOLEAN NOT NULL DEFAULT FALSE,
    criado_em  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_cle_link ON campanha_link_eventos (link_id, criado_em);
CREATE INDEX IF NOT EXISTS ix_cle_grupo ON campanha_link_eventos (grupo_id, criado_em);

CREATE TABLE IF NOT EXISTS grupo_eventos (
    id                 BIGSERIAL PRIMARY KEY,
    grupo_id           INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    tipo               VARCHAR(8) NOT NULL,                        -- entrada|saida
    origem             VARCHAR(16) NOT NULL DEFAULT 'desconhecida', -- link|organica|desconhecida
    link_evento_id     BIGINT REFERENCES campanha_link_eventos(id) ON DELETE SET NULL,
    identificador_hash VARCHAR(64) NOT NULL,   -- sha256(jid+salt); número cru NUNCA persiste
    criado_em          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ge_grupo ON grupo_eventos (grupo_id, criado_em);
-- "entraram e ficaram" = casar entrada e saída do MESMO identificador.
CREATE INDEX IF NOT EXISTS ix_ge_ident ON grupo_eventos (grupo_id, identificador_hash);

CREATE TABLE IF NOT EXISTS grupo_snapshots (
    grupo_id      INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    data          DATE NOT NULL,
    participantes INTEGER NOT NULL DEFAULT 0,
    admins        INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (grupo_id, data)
);

-- RLS: nenhuma destas tem user_id (o dono chega por join). ENABLE sem policy
-- nega Data API/anon; o backend, dono das tabelas, acessa por ownership.
ALTER TABLE campanha_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE campanha_link_eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE grupo_eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE grupo_snapshots ENABLE ROW LEVEL SECURITY;

COMMIT;
