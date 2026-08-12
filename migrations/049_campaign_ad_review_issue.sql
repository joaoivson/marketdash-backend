-- Migration 049: ad_review_issue em campaigns
--
-- Campanha reprovada na moderação de anúncio (conteúdo rejeitado pela Meta)
-- continua com status/effective_status = ACTIVE no nível da campanha — a
-- Meta não rebaixa o status da campanha quando é o ANÚNCIO que é reprovado.
-- Sem isso, uma campanha assim conta pra sempre como "ativa" no card
-- Orçamento/dia mesmo nunca tendo entregue nada (zero gasto/clique/
-- impressão), inflando a contagem e o orçamento somado — o Gerenciador de
-- Anúncios mostra "recentemente rejeitada" porque agrega o status de
-- moderação do anúncio, que só é visível via o campo `issues_info` da
-- Graph API (não estava sendo buscado antes desta migration).
--
-- ad_review_issue = NULL quando não há problema reportado pela Meta.
--
-- Aditiva: coluna nova, nullable, não quebra nada existente.

ALTER TABLE campaigns
  ADD COLUMN IF NOT EXISTS ad_review_issue VARCHAR(255);
