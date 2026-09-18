-- 087 — colunas que antes eram criadas no STARTUP da aplicação
-- (app/db/base.py::_apply_safe_migrations), removidas de lá no hotfix do
-- incidente de 18/09/2026. Idempotente: pode rodar em base que já as tem.
-- Em produção estas colunas já existem (o startup as criou meses atrás);
-- a migration está aqui para HML/dev e para o histórico do schema.

ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS facebook_pixel_id VARCHAR;
ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_json TEXT;
