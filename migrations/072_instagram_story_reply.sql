-- Migration 072: Automação em STORY (reply de story → DM automática)
--
-- Story não tem comentário: a interação chega pelo webhook `messages` como uma
-- DM com o metadado reply_to.story {id, url}. Reaproveitamos instagram_events
-- como registro/trava de duplicidade, com duas mudanças:
--
-- 1. `comment_id` guarda o `mid` da mensagem quando o evento é reply de story.
--    Mids da Meta são longos (base64, passa fácil de 64 chars) — o VARCHAR(64)
--    de comentário não comporta. 160 cobre com folga.
-- 2. `tipo` separa as duas origens ('comentario' | 'story_reply') sem quebrar
--    nenhuma query existente (default preenche o legado).
--
-- ⚠️ ORDEM: aplicar em HML e PRODUÇÃO **antes** do deploy que importa o model —
-- é ALTER TABLE, a armadilha INVERSA do create_all (ver runbook §0): o boot NÃO
-- adiciona coluna em tabela existente, e o INSERT com `tipo` estoura
-- UndefinedColumn.

ALTER TABLE instagram_events ALTER COLUMN comment_id TYPE VARCHAR(160);

ALTER TABLE instagram_events
    ADD COLUMN IF NOT EXISTS tipo VARCHAR(16) NOT NULL DEFAULT 'comentario';

COMMENT ON COLUMN instagram_events.comment_id IS
    'comment_id do comentário OU mid da mensagem (reply de story). UNIQUE = trava de duplicidade.';
COMMENT ON COLUMN instagram_events.tipo IS
    'comentario | story_reply — origem do evento.';
