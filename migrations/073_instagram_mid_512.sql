-- Migration 073: mid real da Meta estourou o VARCHAR(160) da 072.
--
-- Descoberto NO PRIMEIRO reply de story real em produção (02/09/2026 14:52
-- UTC): o mid veio com ~180+ caracteres
-- (aWdfZAG1faXRlbTox... = base64 de ig_dm_item:1:IGMessageID:<igid>:<thread>:<msg>)
-- e o INSERT falhou com StringDataRightTruncation — a task ficou em retry e a
-- DM não saía. 512 dá folga real; continua indexável pelo UNIQUE sem chegar
-- perto do limite do btree.
--
-- ⚠️ APLICADA À MÃO em prod E hml às ~11:58 BRT de 02/09 (corrida contra o
-- último retry da task) — este arquivo versiona o que já está nos dois bancos.

ALTER TABLE instagram_events ALTER COLUMN comment_id TYPE VARCHAR(512);
