-- Migration 075: nomes das contas de anúncio selecionadas (Facebook §4.2).
--
-- O GET /facebook/status passa a devolver id+nome das contas SELECIONADAS sem
-- consultar a Graph — antes o nome só existia ao vivo em GET /ad-accounts, e a
-- tela de configurações mostrava só o "act_123" cru. O PUT /ad-accounts agora
-- persiste, junto da seleção, um dict JSON {"act_123": "Nome"} nesta coluna.
--
-- A seleção em si continua em ad_accounts_json (lista de ids) — isto é só
-- metadado de exibição. Seleção gravada antes desta coluna fica sem nome
-- (status devolve name null) até a usuária re-salvar a seleção.
--
-- Idempotente; também aplicada no boot via _apply_safe_migrations (db/base.py),
-- mesmo padrão do ad_accounts_json.

ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_names_json TEXT;
