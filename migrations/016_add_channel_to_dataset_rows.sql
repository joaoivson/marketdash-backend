-- Migration: Adicionar coluna channel ao dataset_rows_v2
-- Armazena a fonte de tráfego (ex: Instagram, YouTube, WhatsApp) vinda da API Shopee (channelType)

ALTER TABLE dataset_rows_v2 ADD COLUMN IF NOT EXISTS channel VARCHAR;
CREATE INDEX IF NOT EXISTS idx_dataset_rows_channel ON dataset_rows_v2(channel);
CREATE INDEX IF NOT EXISTS idx_dataset_rows_user_channel ON dataset_rows_v2(user_id, channel);
