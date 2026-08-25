---
name: "integracoes-marketdash"
description: "Integrações externas do MarketDash: Shopee Afiliados (GraphQL), Facebook/Meta Ads, Instagram (automação de comentários), e Evolution/WhatsApp. Use ao implementar, depurar ou monitorar qualquer sync, conexão OAuth ou chamada a API de terceiro."
---

# Integrações — MarketDash

## Princípios comuns a todas

1. **Token de terceiro é cifrado** (`app/core/encryption.py`) antes de ir
   para o banco. Nunca em claro, nunca em log — nem truncado.
2. **Credencial ausente degrada com 503**, não com 500 nem com resposta
   vazia. `FACEBOOK_APP_ID` vazio já é tratado assim.
3. **Sync grava em `sync_runs`.** Execução que não deixa rastro é execução
   que ninguém consegue auditar depois — e o painel `/admin/sincronizacoes`
   existe exatamente por isso.
4. **Sync é upsert aditivo.** Nunca apague a janela antes de reescrever:
   janela incompleta da API apaga dado bom.
5. **Task de sync usa priority 9** (batch); botão manual do usuário usa
   **priority 0**. Nunca 5 — some em silêncio.
6. **Cron é `pg_cron` no Supabase** chamando `/internal/cron/*`, 1×/dia.

## Shopee Afiliados

`shopee_graphql_client.py` · `shopee_integration_service.py` ·
`shopee_tasks.py` · rotas em `shopee.py`

- API **GraphQL** com AppID + secret por usuário (cifrados). O AppID é
  **numérico** — valide antes de gravar; AppID inválido dá falha opaca.
- Dois modos: **full refresh** (pesado, batch) e **incremental** (janela
  curta). O manual por período existe para a usuária corrigir buraco.
- Traz comissões e cliques. **O canal real vem dos cliques**, não do pedido.
- Categoria: o produto usa **nível 1** da hierarquia.
- `proxy_graphql` também serve o **Converter** de Meus Links
  (`generateShortLink`).

## Facebook / Meta Ads

`facebook_marketing_client.py` · `facebook_integration_service.py` ·
`facebook_tasks.py` · rotas em `facebook.py`

- OAuth com `FACEBOOK_OAUTH_REDIRECT_URI` / `CONFIG_ID`; versão da Graph API
  fixada em `FACEBOOK_API_VERSION`.
- **Espelho Meta → AdSpend**: gasto e cliques entram em `ad_spends` com
  `source` marcado, guardando backup do valor manual antes de sobrescrever.
- **Campanha arquivada precisa de filtro explícito** na Graph API — sem ele
  o sync simplesmente não a enxerga.
- Campanha "ativa" tem armadilha nos dois sentidos: conta de anúncio
  desmarcada não conta; orçamento vitalício esgotado não conta; **campanha
  reativada volta a contar**. Mexeu nessa lógica → rode
  `test_campaign_active_count_orcamento_esgotado.py` e
  `test_campaign_repository_ad_account_filter.py`.
- Desconectar a integração já teve bug de estado — confirme que o disconnect
  limpa tudo que deveria.

## Instagram

`instagram_connection_service.py` · `instagram_login_client.py` ·
`instagram_automation_service.py` · `instagram_comment_pipeline.py` ·
`instagram_tasks.py` · webhook em `app/api/webhooks/instagram.py`

- Webhook fica em **`/webhooks/instagram`**, fora do `/api/v1`: a URL está
  cadastrada no painel da Meta e não versiona. Rotas de handshake,
  `/deauthorize` e `/data-deletion` também.
- Pipeline: comentário → direct. **Exclusiva do plano MAX.**
- Refresh de token roda por cron (`/internal/cron/instagram-token-refresh`).

## WhatsApp (WAHA)

`waha_client.py` · `whatsapp_*_service.py` · rotas em `whatsapp.py` (resumo +
webhook) e `whatsapp_conexoes.py` (números/grupos das alunas) · runbook
`docs/whatsapp-waha.md`

- **WAHA substituiu a Evolution em 25/08** (engine GOWS/whatsmeow, ~60MB por
  sessão). Sessão = número conectado; nome `mkd{ref4}u{user_id}x{hex4}` roteia
  o webhook e separa hml de prod no mesmo servidor.
- Resumo diário: sessão global `WAHA_SESSAO_RESUMO`, cron
  `/internal/cron/whatsapp-resumo`; eventos `message` (SAIR) +
  `session.status`. Sessões de aluna: SÓ `session.status` (LGPD).
- Sync de grupos cria `sub_id`/`custom_link` na primeira vez (atribuição) e
  grava `sync_runs` com `source="whatsapp_grupos"`.
- No Coolify, **fixe WAHA_DASHBOARD_* e WAHA_API_KEY por env** — sem isso as
  credenciais regeneram a cada start (mesma armadilha da era Evolution).
- **`url_for` atrás do proxy gera webhook em `http`, toma 301 e falha em
  silêncio.** Use `WAHA_WEBHOOK_URL` (URL pública configurada), nunca o
  request.


## Depurar "o sync não trouxe nada"

1. `sync_runs` registrou a execução? Se não, a task não rodou → vá para
   `.claude/rules/celery-filas.md`.
2. Registrou com erro? `sync_error_log` tem o detalhe.
3. Rodou "com sucesso" e trouxe zero? Suspeite de **filtro na API de origem**
   (foi o caso das campanhas arquivadas do Facebook) ou de credencial
   expirada devolvendo lista vazia em vez de erro.
