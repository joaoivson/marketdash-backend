-- 066: Módulo de Grupos — F8: monitoramento de grupos.
--
-- ⚠️ PROTOCOLO: hml ANTES do deploy/model; produção SOMENTE na promoção
-- develop→main (o create_all do boot criaria estas tabelas SEM RLS).
--
-- A afiliada acompanha um grupo (dela ou de terceiro, desde que o número dela
-- seja membro) e replica as ofertas que aparecem lá para os grupos dela, com o
-- link trocado pelo dela.
--
-- LGPD — o que este módulo NÃO guarda, de propósito:
--   * quem escreveu a mensagem. Nenhum JID, telefone ou hash de autor. Só o
--     texto e um hash do próprio texto, para deduplicar repost.
--   * qualquer mensagem que não case com o filtro: o handler descarta ANTES
--     de persistir, não grava-e-filtra.
-- O evento `message` só é assinado nas sessões com monitoramento ativo — é o
-- que impede o conteúdo dos grupos de chegar ao backend quando ninguém pediu.

BEGIN;

CREATE TABLE IF NOT EXISTS monitoramentos (
    id                 SERIAL PRIMARY KEY,
    user_id            INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    nome               VARCHAR(120) NOT NULL,
    -- Grupo de ORIGEM: pode ser de terceiro. A única exigência é que o número
    -- da afiliada seja membro — o que já é verdade para todo grupo sincronizado.
    grupo_origem_id    INTEGER NOT NULL REFERENCES whatsapp_grupos(id) ON DELETE CASCADE,
    -- Sessão que escuta. Guardada explicitamente porque é ela que precisa ter
    -- o evento `message` assinado, e o desligamento tem que saber qual desassinar.
    instancia_id       INTEGER REFERENCES whatsapp_instancias(id) ON DELETE SET NULL,
    -- Destino: uma campanha inteira OU uma seleção de grupos. Os dois nulos =
    -- só captura, não replica (modo observação).
    destino_campanha_id INTEGER REFERENCES campanhas(id) ON DELETE SET NULL,
    destino_grupo_ids  JSONB,
    ativo              BOOLEAN NOT NULL DEFAULT FALSE,
    -- Trocar o link do concorrente pelo dela é o ponto da feature; sem isso a
    -- replicação faria propaganda para outra pessoa.
    converter_links    BOOLEAN NOT NULL DEFAULT TRUE,
    -- Sem link não é oferta. Ligado por padrão para não capturar conversa.
    somente_com_link   BOOLEAN NOT NULL DEFAULT TRUE,
    palavras_chave     JSONB,
    -- FALSE = a captura espera revisão. É o padrão de propósito: replicar
    -- automaticamente o que outra pessoa escreveu, sem ler, é como um grupo
    -- inteiro recebe o que ninguém aprovou.
    replicar_automaticamente BOOLEAN NOT NULL DEFAULT FALSE,
    criado_em          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    atualizado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- O handler do webhook resolve "esta sessão tem monitoramento ativo?" a cada
-- mensagem: é o caminho mais quente do módulo.
CREATE INDEX IF NOT EXISTS ix_monitoramentos_origem_ativo
    ON monitoramentos (grupo_origem_id) WHERE ativo;
CREATE INDEX IF NOT EXISTS ix_monitoramentos_user
    ON monitoramentos (user_id);

CREATE TABLE IF NOT EXISTS monitoramento_capturas (
    id               BIGSERIAL PRIMARY KEY,
    monitoramento_id INTEGER NOT NULL REFERENCES monitoramentos(id) ON DELETE CASCADE,
    -- sha256 do texto normalizado. Repost da MESMA oferta no grupo de origem
    -- (comum: o dono repete de hora em hora) não vira envio duplicado.
    mensagem_hash    VARCHAR(64) NOT NULL,
    texto_original   TEXT,
    texto_final      TEXT,
    link_original    TEXT,
    link_convertido  TEXT,
    -- capturada | replicando | replicada | ignorada | erro
    -- `replicando` é o estado do claim atômico: quem consegue mover
    -- capturada→replicando é o único worker que replica. Sem ele, dois
    -- workers leem 'capturada' juntos e a oferta sai duas vezes.
    status           VARCHAR(20) NOT NULL DEFAULT 'capturada',
    motivo           VARCHAR(200),
    -- Envio rápido gerado pela replicação; NULL enquanto não replicou.
    roteiro_id       INTEGER REFERENCES roteiros(id) ON DELETE SET NULL,
    criado_em        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    replicado_em     TIMESTAMPTZ,
    CONSTRAINT uq_captura_por_monitoramento UNIQUE (monitoramento_id, mensagem_hash)
);
CREATE INDEX IF NOT EXISTS ix_capturas_monitoramento_criado
    ON monitoramento_capturas (monitoramento_id, criado_em DESC);

-- RLS: `monitoramentos` tem user_id → policy (padrão da 052/062).
ALTER TABLE monitoramentos ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS monitoramentos_iso ON monitoramentos;
CREATE POLICY monitoramentos_iso ON monitoramentos
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- `monitoramento_capturas` não tem user_id (o dono chega por join): ENABLE sem
-- policy nega Data API/anon, e o backend acessa por ownership. Mesmo critério
-- da 063.
ALTER TABLE monitoramento_capturas ENABLE ROW LEVEL SECURITY;

COMMIT;
