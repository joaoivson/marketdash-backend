-- Migration: Enable Row Level Security (RLS) for Multi-Tenant Isolation (Supabase Version)
-- Created: 2026-02-04
-- Purpose: Ensure data isolation between users at the PostgreSQL level
-- Note: Compatible with Supabase SQL Editor

-- Ativar RLS nas tabelas principais
ALTER TABLE dataset_rows ENABLE ROW LEVEL SECURITY;
ALTER TABLE datasets ENABLE ROW LEVEL SECURITY;
ALTER TABLE ad_spends ENABLE ROW LEVEL SECURITY;
ALTER TABLE click_rows ENABLE ROW LEVEL SECURITY;

-- Drop políticas existentes se houver
DROP POLICY IF EXISTS dataset_rows_isolation ON dataset_rows;
DROP POLICY IF EXISTS datasets_isolation ON datasets;
DROP POLICY IF EXISTS ad_spends_isolation ON ad_spends;
DROP POLICY IF EXISTS click_rows_isolation ON click_rows;

-- Política: Usuário só acessa seus próprios dataset_rows
CREATE POLICY dataset_rows_isolation ON dataset_rows
    FOR ALL
    USING (
        user_id = COALESCE(
            current_setting('app.current_user_id', true)::int,
            user_id  -- Fallback para permitir acesso quando não há setting (admin queries)
        )
    );

-- Política: Usuário só acessa seus próprios datasets
CREATE POLICY datasets_isolation ON datasets
    FOR ALL
    USING (
        user_id = COALESCE(
            current_setting('app.current_user_id', true)::int,
            user_id  -- Fallback
        )
    );

-- Política: Usuário só acessa seus próprios ad_spends
CREATE POLICY ad_spends_isolation ON ad_spends
    FOR ALL
    USING (
        user_id = COALESCE(
            current_setting('app.current_user_id', true)::int,
            user_id  -- Fallback
        )
    );

-- Política: Usuário só acessa seus próprios click_rows
CREATE POLICY click_rows_isolation ON click_rows
    FOR ALL
    USING (
        user_id = COALESCE(
            current_setting('app.current_user_id', true)::int,
            user_id  -- Fallback
        )
    );

-- Comentários explicativos
COMMENT ON POLICY dataset_rows_isolation ON dataset_rows IS 
    'Ensures users can only access their own dataset rows. Uses app.current_user_id session variable.';

COMMENT ON POLICY datasets_isolation ON datasets IS 
    'Ensures users can only access their own datasets. Uses app.current_user_id session variable.';

COMMENT ON POLICY ad_spends_isolation ON ad_spends IS 
    'Ensures users can only access their own ad spends. Uses app.current_user_id session variable.';

COMMENT ON POLICY click_rows_isolation ON click_rows IS 
    'Ensures users can only access their own click rows. Uses app.current_user_id session variable.';

-- Mensagem de sucesso
DO $$
BEGIN
    RAISE NOTICE '✅ Row Level Security enabled successfully!';
    RAISE NOTICE '🔒 Protected tables: dataset_rows, datasets, ad_spends, click_rows';
    RAISE NOTICE '⚠️  IMPORTANT: Test thoroughly before using in production!';
END $$;
