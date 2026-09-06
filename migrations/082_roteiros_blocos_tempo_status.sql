-- 082: Roteiros — modelo de tempo por passo, passo com N blocos, ações novas.
--
-- ⚠️ PROTOCOLO (mesmo da 058/074/079/080/081): aplicar em HML E PRODUÇÃO
-- **antes** do deploy que importa os models. `create_all` cria TABELA nova
-- (sem RLS!) mas NÃO adiciona coluna a tabela existente — daí a ordem.
--
-- Contexto: o roteiro nasceu com uma data-âncora GLOBAL na execução e os
-- passos derivando dela. Lançamento não é assim — abertura de carrinho,
-- virada de lote e fechamento são data e hora absolutas, e não existe offset
-- que resolva. A partir daqui cada passo de hora fixa carrega a própria data,
-- e `roteiro_execucoes.data_ancora` vira só o registro de QUANDO foi agendado
-- (mantida NOT NULL para não quebrar linha antiga; ninguém mais resolve por
-- ela).
--
-- Seis mudanças, uma transação.

BEGIN;

-- (a) offset com UNIDADE -----------------------------------------------------
--
-- `+X segundos|minutos|horas`. O canônico virou `offset_segundos`;
-- `offset_unidade` guarda só a INTENÇÃO de exibição — 90s tem que voltar como
-- "+90 segundos", não como "+1,5 min". `offset_minutos` fica na tabela como
-- legado e para de ser lido pelo model.

ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS offset_segundos INTEGER;
ALTER TABLE roteiro_passos ADD COLUMN IF NOT EXISTS offset_unidade VARCHAR(10);

-- Idempotente. Todo offset gravado até aqui foi digitado em minutos.
UPDATE roteiro_passos
   SET offset_segundos = COALESCE(offset_segundos, offset_minutos * 60),
       offset_unidade  = COALESCE(offset_unidade, 'minutos')
 WHERE tipo_tempo = 'relativo';

-- (b) passo com mais de uma mensagem ----------------------------------------
--
-- Um envio real é frequentemente 4 imagens + um texto, saindo juntos. Isso é
-- diferente de criar 5 passos com `+0s`: os blocos compartilham horário e
-- grupos, são editados e pré-visualizados juntos, e movem juntos na ordem.
-- O passo continua dono do QUANDO, do PARA QUEM e do marcar todos; os blocos
-- são o QUE sai, em sequência.

CREATE TABLE IF NOT EXISTS passo_blocos (
    id          SERIAL PRIMARY KEY,
    passo_id    INTEGER NOT NULL REFERENCES roteiro_passos(id) ON DELETE CASCADE,
    ordem       INTEGER NOT NULL,
    tipo        VARCHAR(12) NOT NULL,   -- texto|imagem|audio|video|oferta
    conteudo    TEXT,                   -- texto do bloco, ou URL da mídia
    legenda     TEXT,                   -- legenda que acompanha a mídia
    template_id INTEGER REFERENCES templates_mensagem(id) ON DELETE SET NULL,
    criado_em   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_passo_blocos_passo ON passo_blocos (passo_id, ordem);
ALTER TABLE passo_blocos ENABLE ROW LEVEL SECURITY;   -- acesso só via join (padrão 060)

-- Passo antigo = passo de um bloco só. Fazer a conversão aqui, e não com um
-- fallback no motor, mantém UM caminho de envio: o motor lê blocos e ponto.
-- O NOT EXISTS torna a migration re-executável.
INSERT INTO passo_blocos (passo_id, ordem, tipo, conteudo, legenda, template_id)
SELECT p.id, 1,
       CASE p.tipo_conteudo WHEN 'midia' THEN 'imagem' ELSE 'texto' END,
       CASE p.tipo_conteudo WHEN 'midia' THEN p.midia_url ELSE p.texto END,
       CASE p.tipo_conteudo WHEN 'midia' THEN p.texto ELSE NULL END,
       p.template_id
  FROM roteiro_passos p
 WHERE p.tipo_conteudo IN ('texto', 'midia')
   AND NOT EXISTS (SELECT 1 FROM passo_blocos b WHERE b.passo_id = p.id);

-- `texto` e `midia` deixam de ser tipos de passo e viram tipos de BLOCO. O
-- passo passa a ser `mensagem` (container). `oferta` fica exatamente como
-- está — a fila de ofertas não é desta rodada — e `acao_grupo` também.
UPDATE roteiro_passos SET tipo_conteudo = 'mensagem'
 WHERE tipo_conteudo IN ('texto', 'midia');

-- (c) retomada por bloco -----------------------------------------------------
--
-- A linha de `roteiro_mensagens` continua sendo uma por (passo × grupo) — ela
-- é a entrega do passo àquele grupo. Com N blocos, falhar no bloco 3 e
-- reenviar do zero mandaria os blocos 1 e 2 DE NOVO no grupo. Mensagem
-- repetida em grupo é o erro que a afiliada vê e que o WhatsApp pune, então o
-- reenvio retoma de onde parou.
ALTER TABLE roteiro_mensagens ADD COLUMN IF NOT EXISTS blocos_enviados INTEGER NOT NULL DEFAULT 0;

-- (d) data própria em todo passo de hora fixa --------------------------------
--
-- Sem backfill, o primeiro `salvar` depois do deploy recusaria TODO roteiro
-- montado antes dele. A melhor data conhecida é a da última execução agendada
-- — era exatamente o que a âncora global resolvia. Sem execução nenhuma, a
-- data de criação do roteiro: fica no passado, e é justamente o que a nova
-- trava manda mostrar em vermelho em vez de disparar às cegas.
UPDATE roteiro_passos p
   SET data_fixa = COALESCE(
        (SELECT e.data_ancora FROM roteiro_execucoes e
          WHERE e.roteiro_id = p.roteiro_id
          ORDER BY e.criado_em DESC LIMIT 1),
        (SELECT r.criado_em::date FROM roteiros r WHERE r.id = p.roteiro_id)
   )
 WHERE p.tipo_tempo = 'ancora' AND p.data_fixa IS NULL;

-- (e) ações do grupo ---------------------------------------------------------
--
-- `abrir_entrada`/`fechar_entrada` saem: fecha o quê? O grupo daquele passo, o
-- toggle "Aberto" da aba Grupos, ou o link de entrada da campanha? Três
-- controles de nome parecido governando coisas diferentes. Entram
-- `alterar_descricao` e `alterar_imagem`.
--
-- `acao` continua polimórfico e sem CHECK — a validação vive no Pydantic, que
-- é onde a afiliada recebe a mensagem em português. Aqui só se marca o legado:
-- a linha continua existindo, o editor a mostra como "ação removida" e o motor
-- a pula com motivo claro, em vez de sumir em silêncio de dentro de um roteiro
-- que ela montou.
ALTER TABLE roteiro_passos
    ADD COLUMN IF NOT EXISTS acao_descontinuada BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE roteiro_passos
   SET acao_descontinuada = TRUE
 WHERE tipo_conteudo = 'acao_grupo'
   AND acao IN ('abrir_entrada', 'fechar_entrada');

-- (f) uma execução ativa por roteiro ----------------------------------------
--
-- O bug do chip "Rascunho": depois de agendar, o botão "Agendar" continuava na
-- linha e a tela não mudava de estado. Em 06/09 o mesmo roteiro foi agendado
-- TRÊS vezes em 16 segundos — se as mensagens não tivessem sido apagadas por
-- outro bug no mesmo minuto, cada grupo teria recebido tudo em triplicado.
-- O 409 do service é a primeira barreira; este índice é a que não depende de
-- quem chama.
--
-- Se esta linha falhar com "could not create unique index", existe roteiro com
-- mais de uma execução ativa: cancele as sobrando ANTES de rodar a migration,
-- nunca com força bruta aqui dentro.
CREATE UNIQUE INDEX IF NOT EXISTS uq_roteiro_execucao_ativa
    ON roteiro_execucoes (roteiro_id)
    WHERE status IN ('agendada', 'enviando', 'pausada');

COMMIT;
