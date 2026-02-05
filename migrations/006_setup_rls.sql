-- migration_006_setup_rls.sql
-- Ativa Row Level Security e cria políticas baseadas em session variables
-- Versão compatível com ambiente Local (Docker) e Supabase Cloud

-- 1. Ativar RLS nas tabelas críticas
ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE ad_spends ENABLE ROW LEVEL SECURITY;
-- Tabelas v2 detectadas
ALTER TABLE click_rows_v2 ENABLE ROW LEVEL SECURITY;
ALTER TABLE dataset_rows_v2 ENABLE ROW LEVEL SECURITY;

-- 2. Remover políticas existentes para limpeza
DROP POLICY IF EXISTS datasets_iso ON datasets;
DROP POLICY IF EXISTS rows_iso ON dataset_rows;
DROP POLICY IF EXISTS ad_spends_iso ON ad_spends;
DROP POLICY IF EXISTS clicks_v2_iso ON click_rows_v2;
DROP POLICY IF EXISTS rows_v2_iso ON dataset_rows_v2;

-- 3. Criar novas políticas baseadas na variável app.current_user_id
-- O Backend executa: SET LOCAL app.current_user_id = '123';
-- Removido "TO role" para evitar erros de existência de roles específicas em diferentes ambientes.

CREATE POLICY datasets_iso ON datasets 
    FOR ALL 
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

CREATE POLICY rows_iso ON dataset_rows 
    FOR ALL 
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

CREATE POLICY ad_spends_iso ON ad_spends 
    FOR ALL 
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

CREATE POLICY clicks_v2_iso ON click_rows_v2 
    FOR ALL 
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

CREATE POLICY rows_v2_iso ON dataset_rows_v2 
    FOR ALL 
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- 4. Nota: Usuários Superusers (como postgres do Supabase ou dashads_user local com Bypass RLS)
-- continuarão vendo tudo. O RLS se aplicará às conexões standard do app.
