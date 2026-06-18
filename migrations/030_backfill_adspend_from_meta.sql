-- 030_backfill_adspend_from_meta.sql
-- O Dashboard soma a tabela `ad_spends` no card "Gasto Anúncios" (e ROAS/Lucro). O gasto do
-- Meta só entra em `ad_spends` via rebuild_ad_spend_from_meta(), que SÓ roda no fim de um
-- FB sync bem-sucedido. Resultado: usuários com insights anteriores ao espelho, cujo cron não
-- rodou desde o deploy, ou que limparam investimentos, ficam com o Dashboard ZERADO mesmo
-- tendo gasto nas campanhas (a tela de Campanhas lê `campaign_daily_insights`, que tem os dados).
--
-- Este backfill replica EXATAMENTE a lógica do espelho (campaign_repository.rebuild_ad_spend_from_meta)
-- em SQL, para TODOS os usuários de uma vez, a partir dos insights JÁ existentes — sem precisar
-- de um novo sync da API do Meta.
--
-- Regras (idênticas ao espelho da rodada 5):
--  - source='meta' é a projeção pura dos insights; reconstruída do zero (idempotente).
--  - O Meta é AUTORITATIVO nos dias que cobre: substitui o manual desses dias (o manual foi
--    descontinuado e está preservado em ad_spends_manual_backup, migration 028).
--  - Manual de dias NÃO cobertos por insights permanece (não zera o histórico do pessoal).
--  - Só projeta linhas com gasto ou cliques (> 0).
-- Idempotente: pode rodar quantas vezes precisar.

BEGIN;

-- 1. Remove a projeção meta anterior (idempotência).
DELETE FROM ad_spends WHERE source = 'meta';

-- 2. Meta substitui o manual SÓ nos (usuário, dia) cobertos por insights.
DELETE FROM ad_spends a
USING (
    SELECT DISTINCT cdi.user_id, cdi.date
    FROM campaign_daily_insights cdi
    JOIN campaigns c ON c.id = cdi.campaign_id
) cov
WHERE a.source <> 'meta'
  AND a.user_id = cov.user_id
  AND a.date = cov.date;

-- 3. Projeta o gasto/cliques do Meta: soma por (usuário, dia, sub_id da campanha vinculada).
--    sub_id NULL (campanha não vinculada) também entra — conta no total do Dashboard.
INSERT INTO ad_spends (user_id, date, sub_id, amount, clicks, source)
SELECT
    cdi.user_id,
    cdi.date,
    c.sub_id,
    SUM(cdi.spend)::double precision        AS amount,
    COALESCE(SUM(cdi.clicks), 0)::integer   AS clicks,
    'meta'                                  AS source
FROM campaign_daily_insights cdi
JOIN campaigns c ON c.id = cdi.campaign_id
GROUP BY cdi.user_id, cdi.date, c.sub_id
HAVING SUM(cdi.spend) > 0 OR COALESCE(SUM(cdi.clicks), 0) > 0;

COMMIT;

-- Conferência (rode separado): deve haver linhas 'meta' com a soma esperada.
--   SELECT source, count(*), round(sum(amount)::numeric, 2) AS total
--   FROM ad_spends GROUP BY source ORDER BY source;
