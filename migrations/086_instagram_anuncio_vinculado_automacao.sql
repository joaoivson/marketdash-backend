-- 086 — Anúncio vinculado à automação do mesmo produto.
--
-- Por que existe (17/09/2026, @promosdabeatrizz_). Depois da 085 os anúncios
-- apareciam na tela, mas cada automação cobria UMA mídia: o card "Calcinhas
-- Algodão" contava o reel orgânico (5 comentários) enquanto o anúncio do mesmo
-- produto tinha 29. A aluna pensa no PRODUTO, não na mídia: o anúncio é o
-- mesmo vídeo impulsionado, com a mesma legenda e o mesmo link.
--
-- O vínculo mora na mídia detectada (e não numa coluna nova em
-- instagram_automations) por dois motivos: um anúncio pertence a no máximo UMA
-- automação — a coluna garante isso sem tabela de junção —, e ALTER em
-- instagram_automations quebraria qualquer ambiente onde a migration ainda não
-- rodou (UndefinedColumn em toda leitura de automação).

ALTER TABLE instagram_midias_detectadas
    ADD COLUMN IF NOT EXISTS automation_id INTEGER
    REFERENCES instagram_automations(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_ig_midias_detectadas_automacao
    ON instagram_midias_detectadas (automation_id)
    WHERE automation_id IS NOT NULL;
