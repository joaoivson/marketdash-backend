-- Migration 032: Insight de cliques (slice BE-2). FORWARD-ONLY.
--
-- Contexto: custom_links.click_count segue sendo o TOTAL verdadeiro (incrementado pós-dedup/bot
-- no único ponto increment_click_count). Esta tabela grava 1 evento por clique COM timestamp,
-- para alimentar a série temporal (KPIs + gráfico) do insight. Não substitui click_count: é um
-- log paralelo que só cresce a partir de agora. Eventos antigos não existem (forward-only), por
-- isso a UI expõe series_started_at = data do evento mais antigo.

CREATE TABLE IF NOT EXISTS custom_link_events (
    id SERIAL PRIMARY KEY,
    custom_link_id INTEGER NOT NULL REFERENCES custom_links(id) ON DELETE CASCADE,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Índice para a série temporal: filtra por link e ordena/agrupa por tempo.
CREATE INDEX IF NOT EXISTS ix_custom_link_events_link_created
    ON custom_link_events (custom_link_id, created_at);
