-- migration_006_setup_rls_v2.sql
-- Complemento da ativação de RLS para tabelas v2

ALTER TABLE click_rows_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows_v2 ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS clicks_v2_iso ON click_rows_v2;
DROP POLICY IF EXISTS rows_v2_iso ON dataset_rows_v2;

CREATE POLICY clicks_v2_iso ON click_rows_v2 
    FOR ALL 
    TO dashads_user
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

CREATE POLICY rows_v2_iso ON dataset_rows_v2 
    FOR ALL 
    TO dashads_user
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);
