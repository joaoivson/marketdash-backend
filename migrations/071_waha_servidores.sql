-- 071: WAHA multi-servidor — o servidor deixa de ser uma env e vira pool.
--
-- ⚠️ PROTOCOLO (mesmo da 058/067/068): aplicar em HML E PRODUÇÃO **antes** do
-- deploy que importa o model. O `create_all` do boot cria qualquer tabela nova
-- SEM RLS — e esta guarda a API KEY do WAHA, que é a chave de todos os números
-- daquele servidor. "Migration não aplicada" nunca significa "tabela não existe".
--
-- ⚠️ Esta migration tem DUAS metades e a segunda não é opcional: sem o backfill
-- do bloco final, toda sessão viva fica com `servidor_id` nulo. O resolvedor
-- tem fallback para `settings.WAHA_URL`, então nada quebra — mas o cap global
-- passa a contar errado e a alocação não enxerga as sessões existentes.
--
-- Por que a mudança (docs/PLANO_ESCALA_100_USUARIAS.md §2):
--
--  * Hoje o servidor é UM, fixo em `settings.WAHA_URL`. Não existe para onde
--    apontar a sessão 61: cada salto de capacidade vira migração de infra.
--    Com o pool, crescer é INSERT — que é o ponto inteiro do degrau C.
--  * `max_sessoes` é POR SERVIDOR e editável em runtime. A RAM por sessão do
--    WAHA nunca foi medida (medir exige parear vários chips reais de uma vez);
--    deixar o teto no banco troca "chute que precisa estar certo antes de
--    comprar" por "dial que sobe conforme as sessões reais entram".
--  * `aceita_novas` separa "drenar" de "desligar": marcar FALSE para de alocar
--    sessão nova sem derrubar as que já vivem ali.
--
-- ⚠️ SESSÃO NÃO MIGRA DE SERVIDOR. O estado do whatsmeow vive no Postgres
-- daquele WAHA, então mudar `servidor_id` de uma sessão pareada NÃO a move —
-- só faz o backend falar com a caixa errada. Esvaziar servidor = `aceita_novas
-- = FALSE` + rotatividade natural, ou re-pareamento com aviso à afiliada.
--
-- RLS: tabela GLOBAL e administrativa, sem `user_id` — não há política por
-- usuária a escrever. `ENABLE ROW LEVEL SECURITY` sem policy é o estado mais
-- restritivo possível (nega tudo para quem não é dono da tabela), igual ao que
-- a 068 faz com `whatsapp_proxies`. Credencial de servidor não deve ser legível
-- por role de aplicação que não seja a nossa.

BEGIN;

CREATE TABLE IF NOT EXISTS waha_servidores (
    id              SERIAL PRIMARY KEY,
    rotulo          VARCHAR(60)  NOT NULL UNIQUE,  -- "waha-01" (aparece no admin)
    -- Rede INTERNA (Coolify) ou IP de VPN. Nunca porta pública: a X-Api-Key do
    -- WAHA é a chave de todos os números daquele servidor.
    base_url        VARCHAR(255) NOT NULL,
    -- Fernet (app/core/encryption.py), mesma chave dos tokens da Shopee e das
    -- credenciais de proxy. Nunca sai em claro — nem em log, nem em resposta
    -- de API, nem em mensagem de erro.
    api_key_cifrada TEXT         NOT NULL,
    -- Teto DESTE servidor. Conta só instância ATIVA (status <> 'removida'),
    -- igual ao max_sessoes do pool de proxy.
    max_sessoes     INTEGER      NOT NULL DEFAULT 60,
    ativo           BOOLEAN      NOT NULL DEFAULT TRUE,
    -- Drenar sem desligar: FALSE para de receber sessão nova, as atuais ficam.
    aceita_novas    BOOLEAN      NOT NULL DEFAULT TRUE,
    status          VARCHAR(16)  NOT NULL DEFAULT 'ok',   -- ok | degradado | fora
    falhas_seguidas INTEGER      NOT NULL DEFAULT 0,
    ultimo_erro     TEXT,
    verificado_em   TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE whatsapp_instancias
    ADD COLUMN IF NOT EXISTS servidor_id INTEGER REFERENCES waha_servidores(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_whatsapp_instancias_servidor
    ON whatsapp_instancias (servidor_id);

ALTER TABLE waha_servidores ENABLE ROW LEVEL SECURITY;

COMMIT;

-- ---------------------------------------------------------------------------
-- BACKFILL — rodar LOGO APÓS o bloco acima, no mesmo dia.
--
-- Não dá para fazer em SQL puro: `api_key_cifrada` exige a chave Fernet da
-- aplicação, que não existe dentro do Postgres. Use o script, que lê as envs
-- atuais (WAHA_URL/WAHA_API_KEY), cria a linha "waha-01" e aponta TODAS as
-- instâncias não removidas para ela:
--
--     PYTHONPATH=$PWD python scripts/backfill_waha_servidor.py
--
-- Ele é idempotente (não duplica a linha nem repinta quem já tem servidor).
--
-- Conferência depois de rodar — as duas devem bater:
--
--     SELECT count(*) FROM whatsapp_instancias
--      WHERE status <> 'removida' AND servidor_id IS NULL;   -- espera 0
--     SELECT rotulo, max_sessoes,
--            (SELECT count(*) FROM whatsapp_instancias i
--              WHERE i.servidor_id = s.id AND i.status <> 'removida') AS ativas
--       FROM waha_servidores s;
-- ---------------------------------------------------------------------------
