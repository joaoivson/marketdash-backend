-- 085 — Mídias comentadas que não são posts do feed (anúncios), detectadas pelo webhook.
--
-- Por que existe (17/09/2026, @promosdabeatrizz_). A publicação tinha 15
-- comentários e a aluna via "50+": os outros ~200 estavam em ANÚNCIOS. Anúncio
-- criado no Gerenciador (dark post) é outra mídia, com outro id, e NÃO aparece em
-- GET /me/media — a tela de seleção nunca o mostrava, então não havia como criar
-- automação para ele. Todo comentário nele caía em "nenhuma automação cobre este
-- post".
--
-- O único jeito de descobrir essa mídia é o próprio comentário (é o que a
-- InstaMagic faz: "após o PRIMEIRO comentário, ele aparece com a tag Anúncio").
-- Esta tabela guarda a mídia na primeira vez que ela é comentada, e a tela de
-- seleção passa a oferecê-la com a tag.
--
-- eh_anuncio:
--   TRUE  — veio `ad_id` no webhook, ou a mídia não está entre as orgânicas
--   FALSE — é post do feed (conferido na listagem): não aparece como anúncio
--   NULL  — ainda não conferido (resolvido na primeira listagem)

CREATE TABLE IF NOT EXISTS instagram_midias_detectadas (
    id                     BIGSERIAL PRIMARY KEY,
    user_id                INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    connection_id          INTEGER NOT NULL REFERENCES instagram_connections(id) ON DELETE CASCADE,
    media_id               VARCHAR(128) NOT NULL,
    ad_id                  VARCHAR(64),
    ad_title               TEXT,
    original_media_id      VARCHAR(128),
    eh_anuncio             BOOLEAN,
    -- Retrato da Graph API (GET /{media_id}); a URL da miniatura expira e é relida.
    caption                TEXT,
    permalink              TEXT,
    thumbnail_url          TEXT,
    media_type             VARCHAR(32),
    media_product_type     VARCHAR(32),
    media_timestamp        VARCHAR(40),
    metadados_lidos_em     TIMESTAMPTZ,
    metadados_erro         TEXT,
    comentarios            INTEGER NOT NULL DEFAULT 0,
    primeiro_comentario_em TIMESTAMPTZ,
    ultimo_comentario_em   TIMESTAMPTZ,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ig_midia_detectada UNIQUE (connection_id, media_id)
);

CREATE INDEX IF NOT EXISTS ix_ig_midias_detectadas_user
    ON instagram_midias_detectadas (user_id, ultimo_comentario_em DESC);

ALTER TABLE instagram_midias_detectadas ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS instagram_midias_detectadas_iso ON instagram_midias_detectadas;
CREATE POLICY instagram_midias_detectadas_iso ON instagram_midias_detectadas
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- Backfill a partir do ledger (083): toda mídia que recebeu comentário de
-- TERCEIRO e não tem automação de post apontando para ela. eh_anuncio fica NULL
-- e a primeira listagem confere contra as orgânicas. Idempotente.
INSERT INTO instagram_midias_detectadas
    (user_id, connection_id, media_id, comentarios, primeiro_comentario_em, ultimo_comentario_em)
SELECT c.user_id, c.id, e.media_id, COUNT(*), MIN(e.recebido_em), MAX(e.recebido_em)
FROM instagram_webhook_entregas e
JOIN instagram_connections c ON c.ig_user_id = e.ig_user_id
WHERE e.tipo = 'comentario'
  AND e.media_id IS NOT NULL
  AND COALESCE(e.detalhe, '') <> 'comentário da própria conta'
  AND NOT EXISTS (
      SELECT 1 FROM instagram_automations a
      WHERE a.connection_id = c.id AND a.media_id = e.media_id
  )
GROUP BY c.user_id, c.id, e.media_id
ON CONFLICT (connection_id, media_id) DO NOTHING;

COMMENT ON TABLE instagram_midias_detectadas IS
    'Mídias comentadas descobertas pelo webhook — em especial anúncios, que não aparecem em /me/media.';
