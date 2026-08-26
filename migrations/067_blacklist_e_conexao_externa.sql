-- 067: Módulo de Grupos — itens 17 e 18 da spec.
--
-- ⚠️ PROTOCOLO: hml ANTES do deploy/model; produção SOMENTE na promoção
-- develop→main (o create_all do boot criaria estas tabelas SEM RLS).
--
-- (17) Blacklist de números: a tabela nasceu na 060 e ficou inerte — sem
--      repository, service, rota ou tela, e ninguém a lia no envio. Aqui ela
--      ganha o que faltava para ser usável de verdade.
-- (18) Link de conexão externa: a afiliada gera um link temporário para outra
--      pessoa (uma assistente, o dono do número) escanear o QR sem ter acesso
--      à conta dela no MarketDash.

BEGIN;

-- ---------------------------------------------------------------- blacklist
-- O número é guardado como HMAC (irreversível, mesmo segredo dos eventos de
-- grupo). Mas uma lista onde ela não reconhece ninguém é inútil: "3 números
-- bloqueados" sem saber quais não ajuda. `numero_mascarado` guarda o suficiente
-- para reconhecer ("+55 11 ****-4321") e não o suficiente para virar lista de
-- telefones se o banco vazar.
ALTER TABLE blacklist_numeros
    ADD COLUMN IF NOT EXISTS numero_mascarado VARCHAR(24);

-- Ao detectar a entrada de um número da lista, o sistema o remove do grupo —
-- mas só quando a afiliada é admin ali. A flag existe por entrada porque
-- "não quero que receba" e "quero fora dos meus grupos" são pedidos diferentes.
ALTER TABLE blacklist_numeros
    ADD COLUMN IF NOT EXISTS remover_dos_grupos BOOLEAN NOT NULL DEFAULT TRUE;

-- O caminho quente é "este número que acabou de entrar está bloqueado?".
CREATE INDEX IF NOT EXISTS ix_blacklist_user_hash
    ON blacklist_numeros (user_id, numero_hash);

ALTER TABLE blacklist_numeros ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS blacklist_numeros_iso ON blacklist_numeros;
CREATE POLICY blacklist_numeros_iso ON blacklist_numeros
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

-- -------------------------------------------------------- conexão externa
-- Um convite é um token de uso único e vida curta que abre UMA tela pública:
-- o QR daquela sessão, e nada mais.
CREATE TABLE IF NOT EXISTS conexao_convites (
    id           SERIAL PRIMARY KEY,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    instancia_id INTEGER NOT NULL REFERENCES whatsapp_instancias(id) ON DELETE CASCADE,
    -- Só o HASH do token. Quem vê o banco não consegue abrir o link — mesmo
    -- raciocínio de senha: o segredo vive no link que ela mandou, não aqui.
    token_hash   VARCHAR(64) NOT NULL UNIQUE,
    expira_em    TIMESTAMPTZ NOT NULL,
    -- Marcado quando a sessão conecta: o link morre na hora, não no fim do
    -- prazo. Link de pareamento que continua válido depois de pareado é um
    -- convite para outra pessoa conectar OUTRO número no lugar.
    usado_em     TIMESTAMPTZ,
    revogado_em  TIMESTAMPTZ,
    criado_em    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_conexao_convites_instancia
    ON conexao_convites (instancia_id);

ALTER TABLE conexao_convites ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS conexao_convites_iso ON conexao_convites;
CREATE POLICY conexao_convites_iso ON conexao_convites
    FOR ALL
    USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::int);

COMMIT;
