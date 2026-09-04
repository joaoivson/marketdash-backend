-- 079: Campanhas de grupos — rodada de correções (documento delta 03/09).
--
-- ⚠️ PROTOCOLO (mesmo da 058/074): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa os models — `create_all` cria tabela nova SEM RLS e NÃO
-- adiciona coluna em tabela que já existe (o boot-ALTER em db/base.py é a
-- rede extra, não a garantia).
--
-- Três mudanças independentes, uma transação:
--
--   (a) `campanha_numeros` — a campanha declara QUAIS números ela usa. Hoje
--       "Adicionar grupos" oferece grupos de todos os números conectados; um
--       grupo do número A numa campanha que dispara pelo B faz o ENVIO FALHAR,
--       porque B não participa daquele grupo. O escopo passa a ser explícito.
--
--   (b) `campanhas.limite_participantes` — teto por campanha, abaixo da
--       `capacidade` do WhatsApp. Grupo perto de 1024 fica lento, e a afiliada
--       quer margem para quem entra fora do link de entrada.
--
--   (c) `grupo_eventos.identificador` — o número de quem entrou. Mudança de
--       decisão de produto: exportar lead com hash é inútil, e evento já
--       gravado como hash não pode ser revertido. O hash CONTINUA existindo —
--       é ele que casa entrada com saída ("entraram e ficaram") e é dele que
--       o índice ix_ge_ident depende.

BEGIN;

-- (a) ------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS campanha_numeros (
    campanha_id   INTEGER NOT NULL REFERENCES campanhas(id) ON DELETE CASCADE,
    instancia_id  INTEGER NOT NULL REFERENCES whatsapp_instancias(id) ON DELETE CASCADE,
    adicionado_em TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (campanha_id, instancia_id)
);
-- O PK (campanha_id, instancia_id) não serve consulta por instancia_id sozinho
-- — e "quais campanhas usam este número" é exatamente o que a remoção pergunta.
CREATE INDEX IF NOT EXISTS ix_campanha_numeros_instancia
    ON campanha_numeros (instancia_id);

-- Backfill obrigatório: campanha que JÁ EXISTE adota os números dos grupos que
-- já tem. Sem isto, toda campanha viva abre com a aba Grupos vazia e um estado
-- de "selecione um número" — que a usuária lê como bug, não como configuração.
INSERT INTO campanha_numeros (campanha_id, instancia_id)
SELECT DISTINCT cg.campanha_id, gi.instancia_id
  FROM campanha_grupos cg
  JOIN whatsapp_grupo_instancias gi ON gi.grupo_id = cg.grupo_id
ON CONFLICT DO NOTHING;

-- RLS: sem user_id (o dono chega por join em campanhas). ENABLE sem policy
-- nega Data API/anon; o backend, dono das tabelas, acessa por ownership —
-- mesmo padrão de campanha_grupos (059) e campanha_anuncios (065).
ALTER TABLE campanha_numeros ENABLE ROW LEVEL SECURITY;

-- (b) ------------------------------------------------------------------------

-- NULL = sem limite próprio; vale a `capacidade` do grupo. Não é 1024 como
-- default porque "não configurado" e "configurado no máximo" precisam ser
-- distinguíveis: só o NULL acompanha uma capacidade que mude no futuro.
ALTER TABLE campanhas
    ADD COLUMN IF NOT EXISTS limite_participantes INTEGER;

-- (c) ------------------------------------------------------------------------

-- 64 = mesmo tamanho do jid em whatsapp_grupos. Guarda o identificador COMO
-- VEIO do WhatsApp: "5511999999999@c.us" (telefone) ou "84729130@lid" (LID,
-- quando a pessoa tem privacidade ativa).
ALTER TABLE grupo_eventos
    ADD COLUMN IF NOT EXISTS identificador VARCHAR(64);

-- 'telefone' | 'lid'. Sem esta coluna a exportação teria que adivinhar pelo
-- sufixo a cada leitura — e LID exportado como se fosse telefone é pior do que
-- coluna vazia: vira lista de contatos que não existe.
ALTER TABLE grupo_eventos
    ADD COLUMN IF NOT EXISTS identificador_tipo VARCHAR(12);

-- Nullable de propósito: eventos gravados ANTES desta migration só têm o hash,
-- e hash não volta a ser número. A tela mostra vazio, que é a verdade.

COMMIT;
