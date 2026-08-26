-- 065: Módulo de Grupos — F7: vínculo Anúncios×Grupos e Leads do Meta.
--
-- ⚠️ PROTOCOLO: hml ANTES do deploy/model; produção SOMENTE na promoção.
--
-- N campanhas de anúncio → 1 campanha de grupos, por seleção MANUAL (spec
-- §4.7). O concorrente vincula no nível de CONTA de anúncio, o que mistura
-- anúncio de lead com anúncio de venda direta e produz um "CPL Real" que não
-- é real. A afiliada sabe quais anúncios levam ao grupo — ela escolhe.
--
-- `leads` é NULL-able de propósito: NULL = "sem pixel/sem dado" e a tela diz
-- isso; 0 seria mentira ("ninguém virou lead") e vira ticket de suporte.

BEGIN;

CREATE TABLE IF NOT EXISTS campanha_anuncios (
    campanha_id  INTEGER NOT NULL REFERENCES campanhas(id) ON DELETE CASCADE,
    campaign_id  INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    vinculado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campanha_id, campaign_id)
);
-- UNIQUE e não só índice: "N anúncios → 1 campanha de grupos" é um invariante
-- de DINHEIRO. A mesma campanha do Meta vinculada a duas campanhas de grupos
-- faria `gasto_com_imposto` atribuir 100% do MESMO gasto às duas, e os dois
-- lucros sairiam errados sem nada indicar o problema.
--
-- O nome é o que o `create_all` do SQLAlchemy gera para
-- `Column(..., index=True, unique=True)`: nomes diferentes fariam o boot
-- criar um índice IDÊNTICO ao lado deste (ver create_all × produção).
CREATE UNIQUE INDEX IF NOT EXISTS ix_campanha_anuncios_campaign_id
    ON campanha_anuncios (campaign_id);
-- Índice não-único da primeira versão desta migration: redundante com o de
-- cima (não é dado, é estrutura — remover é seguro e reaplicável).
DROP INDEX IF EXISTS ix_campanha_anuncios_campaign;

ALTER TABLE campaign_daily_insights ADD COLUMN IF NOT EXISTS leads INTEGER;

ALTER TABLE campanha_anuncios ENABLE ROW LEVEL SECURITY;

COMMIT;
