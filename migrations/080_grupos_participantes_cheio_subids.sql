-- 080: Campanhas de grupos — rodada de correções (documento delta 04/09).
--
-- ⚠️ PROTOCOLO (mesmo da 058/074/079): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa os models. `create_all` cria tabela nova SEM RLS e NÃO
-- adiciona coluna em tabela que já existe — o boot-ALTER em db/base.py é a
-- rede extra, não a garantia.
--
-- Três mudanças independentes, uma transação:
--
--   (a) `campanha_grupos.cheio_override` — "Cheio" e "Aberto" viram dois
--       eixos. Hoje só existe `aberto`, e o grupo lotado nunca é MARCADO:
--       `aberto` só vira FALSE no ramo em que a campanha inteira esgota, e só
--       com `reabertura_automatica=false` (default é TRUE). Resultado na tela:
--       grupo com 946/900 aparece "Aberto" para sempre.
--
--   (b) `grupo_participantes` — quem está no grupo AGORA. Inverte a decisão de
--       03/09 que mandava descartar a lista de membros: sem ela, "exportar
--       leads" só consegue exportar EVENTOS de entrada, e um grupo de 946
--       pessoas acumuladas em meses exporta 8 linhas. É também o que resolve
--       LID→telefone: o payload REST do WAHA traz `PhoneNumber` ao lado do
--       `JID`, enquanto o webhook de entrada nem sempre traz.
--       **A política de privacidade precisa refletir isso antes da promoção.**
--
--   (c) `campanha_sub_ids` — Sub IDs vinculados à CAMPANHA (vários), somados
--       aos Sub IDs dos grupos dela. Sem isso, comissão de campanha de grupo
--       que não passa por grupo rastreado fica invisível nos Resultados.

BEGIN;

-- (a) ------------------------------------------------------------------------

-- NULLABLE de propósito: três estados, não dois.
--   NULL  = sem override; vale a regra automática
--           participantes >= LEAST(capacidade, COALESCE(limite_participantes, capacidade))
--   TRUE  = a usuária marcou cheio à mão
--   FALSE = a usuária marcou não-cheio à mão (destrava grupo cuja contagem o
--           WhatsApp não atualizou)
--
-- Fica no VÍNCULO e não no grupo porque o mesmo grupo pode estar em N
-- campanhas com limites diferentes — em uma ele estoura o teto, na outra não.
ALTER TABLE campanha_grupos
    ADD COLUMN IF NOT EXISTS cheio_override BOOLEAN;

-- (b) ------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS grupo_participantes (
    grupo_id      INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    -- Identidade COMO VEIO do WhatsApp, igual a grupo_eventos.identificador:
    -- "5511999999999@c.us" ou "84729130@lid". É a chave dentro do grupo.
    identificador VARCHAR(64) NOT NULL,
    -- O telefone resolvido, quando o WhatsApp o informa. Separado do
    -- identificador porque em grupo LID os dois são valores DIFERENTES, e é
    -- justamente essa confusão que fez o export sair com a coluna vazia.
    telefone      VARCHAR(32),
    -- Pseudônimo HMAC — a mesma chave que casa entrada com saída em
    -- grupo_eventos, para dar `data_entrada` a quem já tem evento.
    identificador_hash VARCHAR(64),
    admin         BOOLEAN NOT NULL DEFAULT FALSE,
    -- Primeira vez que este sync viu a pessoa no grupo. NÃO é a data de
    -- entrada real de quem já estava lá antes do módulo existir — por isso a
    -- exportação prefere a data do evento quando ela existe.
    visto_em      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- Último sync que confirmou a presença. O sync apaga quem não apareceu.
    confirmado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (grupo_id, identificador)
);

-- "Quem está neste grupo" é a consulta da exportação; a PK já serve. Este
-- índice serve o caminho inverso — resolver LID→telefone de um evento.
CREATE INDEX IF NOT EXISTS ix_grupo_participantes_hash
    ON grupo_participantes (identificador_hash);

-- RLS: sem user_id (o dono chega por join em whatsapp_grupos). ENABLE sem
-- policy nega Data API/anon; o backend, dono da tabela, acessa por ownership —
-- mesmo padrão de campanha_grupos (059), campanha_anuncios (065) e
-- campanha_numeros (079).
ALTER TABLE grupo_participantes ENABLE ROW LEVEL SECURITY;

-- (c) ------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campanha_sub_ids (
    campanha_id  INTEGER NOT NULL REFERENCES campanhas(id) ON DELETE CASCADE,
    -- Guardado NORMALIZADO (o mesmo normalizar_sub_id do KpiService), senão
    -- "WGEA" e "wgea" viram dois vínculos e a comissão conta duas vezes.
    sub_id       VARCHAR(120) NOT NULL,
    vinculado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campanha_id, sub_id)
);

-- NÃO copiar o UNIQUE GLOBAL de campanha_anuncios (065:28-29). Lá a coluna é
-- FK para `campaigns`, que já é por usuária; aqui `sub_id` é TEXTO LIVRE e um
-- UNIQUE global faria a afiliada A impedir a afiliada B de usar "promo1".
-- A regra de "um sub_id só numa campanha" é validada no service, por usuária.
CREATE INDEX IF NOT EXISTS ix_campanha_sub_ids_sub_id
    ON campanha_sub_ids (sub_id);

ALTER TABLE campanha_sub_ids ENABLE ROW LEVEL SECURITY;

COMMIT;
