-- Migration 056: link e texto do botão da mensagem de direct.
--
-- Rodada 2 dos ajustes. O direct passa a sair como template `button` da Meta:
-- texto + um botão web_url, no lugar do link colado dentro da mensagem. Link
-- cru no meio do texto parece spam; botão parece mensagem de marca (é o que o
-- ManyChat faz).
--
-- `dm_texto` continua sendo a mensagem — só perde o link de dentro. As duas
-- colunas são NULL para não quebrar as automações já criadas: sem link, o envio
-- cai no formato antigo (texto puro), que segue no código como fallback.
--
-- Idempotente: pode rodar de novo sem efeito.

ALTER TABLE instagram_automations
    ADD COLUMN IF NOT EXISTS dm_link TEXT NULL;

ALTER TABLE instagram_automations
    ADD COLUMN IF NOT EXISTS dm_botao_texto VARCHAR(20) NULL;
