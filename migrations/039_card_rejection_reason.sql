-- Migration 039: card_rejection_reason em subscription_events
--
-- Motivo de recusa de cartão (ex.: refused_bank) enviado no webhook Kiwify.
-- Aditiva: coluna nova, nullable, não quebra nada existente.

ALTER TABLE subscription_events
  ADD COLUMN IF NOT EXISTS card_rejection_reason TEXT;
