-- Migration 055: tipo da conta do Instagram na conexão.
--
-- Para bancos que já aplicaram a 052 (é o caso de HML). Em instalação nova a
-- coluna já nasce na 052 e esta migration não faz nada.
--
-- Por quê: `account_type` distingue Business de Criador de Conteúdo. Conta
-- Business não consegue ficar privada no Instagram; Criador consegue — e perfil
-- privado NÃO recebe webhook de comentário. Guardar o tipo permite avisar a aluna
-- de forma preventiva, antes de ela publicar um post achando que a automação vai
-- rodar.

ALTER TABLE instagram_connections
    ADD COLUMN IF NOT EXISTS account_type VARCHAR(32) NULL;
