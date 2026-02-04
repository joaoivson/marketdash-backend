-- Migration: Add Performance Indexes for MarketDash
-- Created: 2026-02-04
-- Purpose: Add composite indexes to support queries for 1000+ concurrent users

-- Índices compostos para queries frequentes do dashboard
-- user_id + date (usado em filtros de período)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_user_date 
    ON dataset_rows (user_id, date DESC);

-- user_id + product (usado em filtros de produto)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_user_product 
    ON dataset_rows (user_id, product);

-- user_id + date para ad_spends
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_ad_spends_user_date 
    ON ad_spends (user_id, date DESC);

-- Índice para agregações de dashboard (KPIs)
-- Covering index para evitar table lookups
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_user_date_metrics 
    ON dataset_rows (user_id, date) 
    INCLUDE (revenue, cost, commission, profit);

-- Índice para deduplicação via row_hash
CREATE UNIQUE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_hash 
    ON dataset_rows (row_hash) 
    WHERE row_hash IS NOT NULL;

-- Índice para queries por plataforma
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_user_platform 
    ON dataset_rows (user_id, platform) 
    WHERE platform IS NOT NULL;

-- Índice para queries por categoria
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_user_category 
    ON dataset_rows (user_id, category) 
    WHERE category IS NOT NULL;

-- Índice para queries por sub_id1 (usado em filtros de campanhas)
CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_dataset_rows_user_sub_id1 
    ON dataset_rows (user_id, sub_id1) 
    WHERE sub_id1 IS NOT NULL;

-- Análise das tabelas para atualizar estatísticas do query planner
ANALYZE dataset_rows;
ANALYZE ad_spends;
ANALYZE datasets;
