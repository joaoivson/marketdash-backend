-- 044: uma geração de diagnóstico por vez, garantida pelo banco.
--
-- O serviço checava `em_andamento()` e só depois criava a sessão. Entre as duas
-- coisas cabe outra requisição: dois cliques rápidos no botão geram duas
-- análises e debitam 20 créditos. Checagem em Python não resolve corrida —
-- índice único resolve.
--
-- O índice é parcial: só existe enquanto a linha está em "gerando". Sessão
-- pronta ou com erro sai do índice, então a afiliada pode gerar quantas
-- análises quiser ao longo do tempo — só não duas ao mesmo tempo.

BEGIN;

-- 1. Sessões presas em "gerando" impediriam a criação do índice e travariam a
--    usuária num 409 permanente. Toda geração dura segundos; passou de 15
--    minutos, o processo morreu no meio.
UPDATE ai_diagnostics
   SET status = 'erro',
       erro_mensagem = COALESCE(erro_mensagem, 'Análise interrompida. Tente de novo.'),
       concluido_em = COALESCE(concluido_em, NOW())
 WHERE status = 'gerando'
   AND criado_em < NOW() - INTERVAL '15 minutes';

-- 2. Se ainda restar mais de uma "gerando" por usuária (duplicata criada pela
--    corrida que este índice fecha), mantém só a mais recente.
UPDATE ai_diagnostics d
   SET status = 'erro',
       erro_mensagem = COALESCE(erro_mensagem, 'Análise duplicada descartada.'),
       concluido_em = COALESCE(concluido_em, NOW())
 WHERE d.status = 'gerando'
   AND EXISTS (
         SELECT 1 FROM ai_diagnostics mais_nova
          WHERE mais_nova.user_id = d.user_id
            AND mais_nova.status = 'gerando'
            AND (mais_nova.criado_em, mais_nova.id) > (d.criado_em, d.id)
       );

CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_diagnostics_gerando_por_usuario
    ON ai_diagnostics (user_id)
 WHERE status = 'gerando';

COMMIT;
