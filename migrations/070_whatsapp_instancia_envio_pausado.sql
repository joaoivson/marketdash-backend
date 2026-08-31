-- 070: Pausar o envio por um número, sem removê-lo.
--
-- ⚠️ PROTOCOLO (mesmo da 058/067/068): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa o model. Aqui o risco é o inverso do de tabela nova: o
-- `create_all` do boot NÃO adiciona coluna em tabela que já existe — subir o
-- model antes desta migration derruba `GET /instancias` com UndefinedColumn.
--
-- Por que coluna nova e não `status = 'pausada'`:
--
--   `status` é a SAÚDE da conexão e quem escreve nele é o webhook do WAHA
--   (`aplicar_evento_de_status`). Uma pausa gravada ali seria apagada no
--   próximo evento `WORKING` — a afiliada pausaria e o chip voltaria a
--   disparar sozinho. Os dois eixos são independentes: um chip pode estar
--   conectado E pausado.
--
--   É o mesmo desenho já usado no pool de proxies (068): `ativo` carrega a
--   intenção humana, `status` carrega a saúde automática.
--
-- `pausado_em` existe para responder "desde quando este chip está parado?"
-- quando a afiliada reclamar que um roteiro não saiu.
--
-- RLS: `whatsapp_instancias` já tem política por usuária desde a 058 — coluna
-- nova em tabela existente herda a policy, não há nada a fazer aqui.

BEGIN;

ALTER TABLE whatsapp_instancias
    ADD COLUMN IF NOT EXISTS envio_pausado BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE whatsapp_instancias
    ADD COLUMN IF NOT EXISTS pausado_em    TIMESTAMPTZ;

COMMIT;
