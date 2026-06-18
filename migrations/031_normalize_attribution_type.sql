-- 031_normalize_attribution_type.sql
-- O KPI "Diretos" compara dataset_rows_v2.attribution_type == 'ORDERED_IN_SAME_SHOP'
-- (constante canônica). Mas a API Shopee grava TEXTO ("Ordered in Same Shop" /
-- "Ordered in Different Shop") e o CSV grava PT ("Pedido na mesma loja" / "...loja diferente").
-- Como o texto cru nunca casa com a constante, "Diretos" dava 0 mesmo havendo venda direta.
--
-- A partir de agora o sync (shopee_integration_service) e o parser de CSV normalizam na gravação.
-- Este backfill converte o que já está no banco. translate(_,' ') transforma underscores em
-- espaços, então "ORDERED_IN_SAME_SHOP" e "Ordered in Same Shop" caem no mesmo LIKE.
-- Idempotente.

-- Direto: mesma loja do clique.
UPDATE dataset_rows_v2
SET attribution_type = 'ORDERED_IN_SAME_SHOP'
WHERE attribution_type IS NOT NULL
  AND attribution_type <> 'ORDERED_IN_SAME_SHOP'
  AND (
        lower(translate(attribution_type, '_', ' ')) LIKE '%same shop%'
     OR lower(attribution_type) LIKE '%mesma loja%'
  );

-- Cookie / cross-shop: comprou em loja diferente da divulgada.
UPDATE dataset_rows_v2
SET attribution_type = 'ORDERED_IN_DIFFERENT_SHOP'
WHERE attribution_type IS NOT NULL
  AND attribution_type <> 'ORDERED_IN_DIFFERENT_SHOP'
  AND (
        lower(translate(attribution_type, '_', ' ')) LIKE '%different shop%'
     OR lower(attribution_type) LIKE '%loja diferente%'
  );

-- Conferência (rode separado): distribuição dos valores após o backfill.
--   SELECT attribution_type, count(*) FROM dataset_rows_v2 GROUP BY attribution_type ORDER BY 2 DESC;
