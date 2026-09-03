-- 074: Grupos — toggle "Ativo" da usuária (spec §6.2/6.3).
--
-- ⚠️ PROTOCOLO (mesmo da 058/067/070): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa o model — `create_all` NÃO adiciona coluna em tabela que
-- já existe (o boot-ALTER em db/base.py é a rede extra).
--
-- Dois eixos que NÃO se misturam:
--
--   `ativo`   = lifecycle do SYNC. Some do WhatsApp → FALSE; reaparece →
--               TRUE, incondicionalmente, em todo sync. Automático.
--   `ativado` = escolha da USUÁRIA (o toggle da tela). O sync NUNCA escreve
--               aqui — gravar o toggle em `ativo` faria o sync da madrugada
--               desfazer a escolha dela.
--
-- Ativar é o PONTO DE ATRIBUIÇÃO: o PATCH que liga o toggle garante sub_id +
-- custom_link na mesma transação (antes disso o grupo nasce sem ambos — o
-- sync deixou de criá-los nesta rodada). Desativar é só a flag: nada é
-- apagado, o histórico de comissão permanece.
--
-- RLS: `whatsapp_grupos` já tem política por usuária desde a 058 — coluna
-- nova em tabela existente herda a policy.

BEGIN;

ALTER TABLE whatsapp_grupos
    ADD COLUMN IF NOT EXISTS ativado BOOLEAN NOT NULL DEFAULT FALSE;

-- Backfill: grupo em uso de fato nasce ativado, para o toggle não desligar
-- operação viva no dia do deploy. São dois usos, não um:
--
--   1. destino de campanha de grupos (`campanha_grupos`);
--   2. ORIGEM de monitoramento (`monitoramentos.grupo_origem_id`) — grupo de
--      terceiro que ela observa. Fora do backfill, `registrar()` passaria a
--      ignorar o webhook desse grupo e o monitoramento morreria em silêncio,
--      que é justamente o modo de falha que esta rodada combate.
--
-- O resto fica FALSE (a usuária liga quando quiser).
UPDATE whatsapp_grupos g
   SET ativado = TRUE
 WHERE EXISTS (
        SELECT 1
          FROM campanha_grupos cg
         WHERE cg.grupo_id = g.id
 )
    OR EXISTS (
        SELECT 1
          FROM monitoramentos m
         WHERE m.grupo_origem_id = g.id
 );

COMMIT;
