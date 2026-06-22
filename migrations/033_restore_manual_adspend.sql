-- 033_restore_manual_adspend.sql
-- Restaura os gastos MANUAIS que a migration 030 (e o bug do item 10) apagaram nos dias em
-- que o Meta veio ZERADO. A 030 deletou o manual dos (usuario, dia) cobertos por insights, mas
-- so reinseriu Meta onde spend/clicks > 0 -> dias com Meta=0 ficaram SEM nenhuma linha (R$0 no
-- Dashboard). O snapshot original esta em ad_spends_manual_backup (migration 028).
--
-- >>> ORDEM OBRIGATORIA <<<
-- Aplique este restore SOMENTE DEPOIS de deployar o backend com o fix do item 10
-- (rebuild_ad_spend_from_meta passou a cobrir SO dias com Meta > 0). O pg_cron roda o rebuild
-- de HORA EM HORA; sem o fix no ar, o proximo sync apaga de novo o que este restore trouxer.
--
-- Seguranca: so restaura (usuario, dia) que HOJE nao tem NENHUMA linha em ad_spends. Assim nao
-- duplica dias ja cobertos pelo Meta (autoritativo), nem o manual que nunca foi apagado. So traz
-- de volta os dias realmente perdidos. Idempotente (pode rodar de novo sem efeito extra).
-- clicks = 0: lancamento manual nunca teve cliques (cliques sao do Meta).

-- ============================================================================
-- 1) DRY-RUN — rode ISOLADO ANTES de aplicar, pra ver o que seria restaurado:
-- ============================================================================
-- SELECT count(*)                              AS linhas,
--        count(DISTINCT b.user_id)             AS usuarios,
--        round(sum(b.amount)::numeric, 2)      AS total_gasto_manual
-- FROM ad_spends_manual_backup b
-- WHERE NOT EXISTS (
--   SELECT 1 FROM ad_spends a WHERE a.user_id = b.user_id AND a.date = b.date
-- );

-- ============================================================================
-- 2) RESTORE
-- ============================================================================
BEGIN;

INSERT INTO ad_spends (user_id, date, sub_id, amount, clicks, source)
SELECT b.user_id, b.date, b.sub_id, b.amount, 0, 'manual'
FROM ad_spends_manual_backup b
WHERE NOT EXISTS (
  SELECT 1 FROM ad_spends a
  WHERE a.user_id = b.user_id AND a.date = b.date
);

COMMIT;

-- ============================================================================
-- 3) Conferencia (rode separado):
-- ============================================================================
-- SELECT source, count(*), round(sum(amount)::numeric, 2) AS total
-- FROM ad_spends GROUP BY source ORDER BY source;
