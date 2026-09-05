-- 081: Campanhas de grupos — medição, rotação e leads (documento de 05/09).
--
-- ⚠️ PROTOCOLO (mesmo da 058/074/079/080): aplicar em HML E PRODUÇÃO **antes**
-- do deploy que importa os models. `create_all` NÃO adiciona coluna nem altera
-- tipo de coluna existente — o boot-ALTER em db/base.py é a rede, não a
-- garantia.
--
-- Duas mudanças independentes, uma transação:
--
--   (a) `campanha_link_eventos.resultado` — o desfecho do roteamento. Nasce
--       porque "Vagas esgotadas" sai do fluxo normal: quando todos os grupos
--       estão cheios o link passa a mandar para o primeiro da ordem, e esse
--       clique PRECISA ser contado. Sem a coluna, o gasto existe no Meta e o
--       clique não existe aqui — e a taxa de entrada MELHORA artificialmente
--       justo quando a operação está pior.
--
--   (b) `whatsapp_grupos.sub_id` de VARCHAR(24) para VARCHAR(64) — o Sub ID
--       legível (`grupobeatriz2k7f`) não cabe em 24 sem cortar o nome no meio.
--       O Postgres NÃO trunca varchar: estoura `value too long`, e a escrita
--       acontece dentro da transação do toggle de ativação — um nome de grupo
--       comprido derrubaria a ativação inteira com 500, e só na conta de quem
--       tem nome grande.

BEGIN;

-- (a) ------------------------------------------------------------------------

-- NULLABLE de propósito: as linhas já gravadas em homologação são todas
-- roteamento normal, e um NOT NULL exigiria backfill para nada. NULL lê-se
-- como "roteado" — a constante existe no model para quem grava daqui em diante.
ALTER TABLE campanha_link_eventos
    ADD COLUMN IF NOT EXISTS resultado VARCHAR(24);

-- "Quantos cliques caíram no fallback" é a pergunta que decide se a campanha
-- precisa de grupo novo. Sem índice ela varre a tabela de cliques inteira.
CREATE INDEX IF NOT EXISTS ix_cle_resultado
    ON campanha_link_eventos (link_id, resultado)
    WHERE resultado IS NOT NULL;

-- (b) ------------------------------------------------------------------------

-- 64 e não 32: "grupo" (5) + nome sanitizado (até ~40) + sufixo (4) ainda
-- sobra folga. Ampliar VARCHAR não reescreve a tabela no Postgres (é mudança
-- só de catálogo desde a 9.2) e o índice único é preservado.
ALTER TABLE whatsapp_grupos
    ALTER COLUMN sub_id TYPE VARCHAR(64);

COMMIT;
