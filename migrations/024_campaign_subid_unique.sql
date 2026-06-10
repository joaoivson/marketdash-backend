-- 024_campaign_subid_unique.sql
-- Garante o vínculo 1:1 entre campanha e Sub ID: um Sub ID só pode pertencer a
-- uma campanha por usuário. Índice parcial (ignora as campanhas sem vínculo).
--
-- A aplicação também valida isso em CampaignService.set_link (erro 409 amigável),
-- mas o índice é a rede de segurança no banco.
--
-- NOTA: se já existir Sub ID duplicado em campanhas diferentes, este índice falha.
-- Resolver os duplicados antes (manter o vínculo correto, desvincular o resto):
--   SELECT user_id, sub_id, count(*) FROM campaigns
--   WHERE sub_id IS NOT NULL GROUP BY user_id, sub_id HAVING count(*) > 1;

CREATE UNIQUE INDEX IF NOT EXISTS uq_campaign_user_subid_notnull
    ON campaigns (user_id, sub_id)
    WHERE sub_id IS NOT NULL;
