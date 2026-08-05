-- Migration 043: Diagnóstico IA
--
-- Três tabelas. O saldo de créditos NÃO é guardado em contador: é derivado da
-- soma do ledger no mês corrente. Contador diverge; ledger audita.

CREATE TABLE IF NOT EXISTS ai_diagnostics (
  id              BIGSERIAL PRIMARY KEY,
  user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  periodo_inicio  DATE NOT NULL,
  periodo_fim     DATE NOT NULL,
  snapshot        JSONB NOT NULL DEFAULT '{}'::jsonb,
  relatorio       JSONB,
  status          TEXT NOT NULL DEFAULT 'gerando',
  erro_mensagem   TEXT,
  modelo          TEXT,
  tokens_entrada  INTEGER,
  tokens_saida    INTEGER,
  criado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  concluido_em    TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ix_ai_diagnostics_user ON ai_diagnostics (user_id, criado_em DESC);

CREATE TABLE IF NOT EXISTS ai_diagnostic_messages (
  id             BIGSERIAL PRIMARY KEY,
  diagnostic_id  BIGINT NOT NULL REFERENCES ai_diagnostics(id) ON DELETE CASCADE,
  papel          TEXT NOT NULL,
  conteudo       TEXT NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ai_messages_diag ON ai_diagnostic_messages (diagnostic_id, criado_em);

CREATE TABLE IF NOT EXISTS ai_credit_ledger (
  id             BIGSERIAL PRIMARY KEY,
  user_id        INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  diagnostic_id  BIGINT REFERENCES ai_diagnostics(id) ON DELETE SET NULL,
  tipo           TEXT NOT NULL,
  creditos       INTEGER NOT NULL,
  saldo_apos     INTEGER NOT NULL,
  criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_ai_ledger_user_mes ON ai_credit_ledger (user_id, criado_em DESC);
