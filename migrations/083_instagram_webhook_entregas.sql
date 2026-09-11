-- 083 — Ledger de entrega do webhook do Instagram.
--
-- Por que existe. Em 11/09/2026 uma conta ficou 2 dias sem responder comentário
-- e NÃO havia como distinguir três coisas completamente diferentes:
--
--   a) a Meta não entregou o webhook;
--   b) entregou e a assinatura foi recusada (403), sem deixar rastro no banco;
--   c) entregou, a task rodou e o pipeline descartou em silêncio — o caminho
--      "nenhuma automação cobre este post" retorna SEM gravar em
--      instagram_events, então some.
--
-- instagram_events só registra o que o pipeline ACEITOU processar. Esta tabela
-- registra o que CHEGOU, uma linha por item (comentário ou reply de story), e a
-- task carimba o desfecho depois. Linha parada em 'enfileirado' = task que nunca
-- executou, que é a quarta falha invisível (fila sem consumidor).

CREATE TABLE IF NOT EXISTS instagram_webhook_entregas (
    id              BIGSERIAL PRIMARY KEY,
    recebido_em     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ig_user_id      VARCHAR(64),
    tipo            VARCHAR(24)  NOT NULL DEFAULT 'outro',
    item_id         VARCHAR(512),
    media_id        VARCHAR(128),
    assinatura_ok   BOOLEAN      NOT NULL DEFAULT TRUE,
    desfecho        VARCHAR(64)  NOT NULL DEFAULT 'enfileirado',
    detalhe         TEXT,
    processado_em   TIMESTAMPTZ
);

-- SEM unique em item_id, de propósito: reentrega da Meta é informação de
-- diagnóstico, não duplicata a ser escondida.
CREATE INDEX IF NOT EXISTS ix_ig_entregas_recebido
    ON instagram_webhook_entregas (recebido_em DESC);
CREATE INDEX IF NOT EXISTS ix_ig_entregas_conta
    ON instagram_webhook_entregas (ig_user_id, recebido_em DESC);
CREATE INDEX IF NOT EXISTS ix_ig_entregas_item
    ON instagram_webhook_entregas (item_id);

-- Sem RLS e sem user_id: a linha nasce ANTES de saber de quem é a conta (o
-- webhook chega identificado só pelo ig_user_id, e no caso de assinatura
-- inválida nem isso). É tabela de operação, lida por admin/SQL, nunca exposta
-- por endpoint de aluna.

COMMENT ON TABLE instagram_webhook_entregas IS
    'O que a Meta ENTREGOU no webhook do Instagram, item a item, com o desfecho carimbado pela task.';
COMMENT ON COLUMN instagram_webhook_entregas.desfecho IS
    'enfileirado (task ainda não rodou) | enviado | sem_match | duplicado | expirado | ignorado | falhou | assinatura_invalida | erro_enfileiramento';
