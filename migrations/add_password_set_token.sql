-- Migration: Adicionar campos de token para definir senha na tabela users
-- Data: 2026-01-26

ALTER TABLE users 
ADD COLUMN IF NOT EXISTS password_set_token VARCHAR(255),
ADD COLUMN IF NOT EXISTS password_set_token_expires_at TIMESTAMP WITH TIME ZONE;

-- Criar índice para melhor performance nas buscas por token
CREATE INDEX IF NOT EXISTS idx_users_password_set_token 
ON users(password_set_token);
