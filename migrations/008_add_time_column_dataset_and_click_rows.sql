-- Migration: Adicionar coluna time (hora) em dataset_rows_v2 e click_rows_v2
-- Permite persistir hora extraída do CSV quando a coluna vier no formato datetime (ex.: 2026-01-07 23:59:22)
-- Data: 2026-02-09

ALTER TABLE dataset_rows_v2 ADD COLUMN IF NOT EXISTS time TIME;
ALTER TABLE click_rows_v2 ADD COLUMN IF NOT EXISTS time TIME;
