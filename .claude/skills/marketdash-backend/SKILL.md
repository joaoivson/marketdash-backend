---
name: "marketdash-backend"
description: "Domínio e arquitetura do backend MarketDash: pipeline de CSV de comissões, KPIs (lucro, ROAS, canal, categoria, SubID), AdSpend, campanhas, cliques, páginas de captura e links. Use ao implementar ou depurar qualquer regra de negócio do produto, ao mexer em services/repositories, e sempre que a pergunta for 'de onde sai esse número'."
---

# MarketDash — Backend

SaaS de analytics para afiliados de marketing digital. A usuária (afiliada)
vende produtos de terceiros, ganha **comissão**, paga **anúncio**, e quer
saber se está no lucro.

## O modelo mental do produto

```
CSV de comissões (Shopee)  ─┐
Sync automático (Shopee)   ─┼→ dataset_rows_v2.raw_data (JSONB)
                            │
Cliques (upload / sync)    ─┼→ click_rows_v2        → canal real, categoria
                            │
Gasto manual + Meta Ads    ─┴→ ad_spends            → custo
                                    ↓
                        Lucro · ROAS · desempenho
```

## O cálculo que define o produto

```
Profit = Commission − Ad Spend
ROAS   = Revenue / Ad Spend
```

**Não é `Revenue − Cost`.** A afiliada não recebe a receita da venda, recebe a
comissão. `Revenue − Cost` daria lucro fantasma.

Campos no `raw_data` do CSV da Shopee:

- Receita: `raw_data["Valor de Compra(R$)"]`
- Comissão: `raw_data["Comissão líquida do afiliado(R$)"]`

## ⚠️ Onde o KPI é realmente calculado

**O `get_kpis` do backend NÃO é o que a usuária vê.** O dashboard calcula os
KPIs **no frontend**, a partir das linhas cruas. Mudar o cálculo aqui e não
mudar lá não muda nada na tela — e mudar só lá deixa os dois divergentes.

Correlato: as colunas `cost` e `profit` de `dataset_rows_v2` estão **mortas**.
Existem, não são fonte de nada, e ler delas dá número errado com cara de certo.

Contagem de pedidos: conte **`order_id` distinto**. Uma venda com vários itens
vira várias linhas.

## Domínios do backend

| Domínio | Rotas | Services principais |
|---|---|---|
| **Dashboard / KPIs** | `dashboard.py` | `dashboard_service`, `kpi_service` |
| **Datasets / CSV** | `datasets.py`, `uploads.py`, `jobs.py` | `csv_service`, `csv_polars`, `dataset_service`, `job_service` |
| **Cliques** | `clicks.py` | `click_service` |
| **Gasto de anúncio** | `ad_spends.py` | `ad_spend_service` |
| **Campanhas** | `campaigns.py` | `campaign_service` |
| **Páginas de captura** | `capture_sites.py`, `page_events.py` | `capture_site_service`, `page_event_service` |
| **Meus Links** | `custom_links.py` | `custom_link_service` |
| **Afiliados (indique)** | `affiliates.py` | `affiliate_service` |
| **Assinatura** | `subscription.py`, `kiwify.py`, `cakto.py`, `payment.py` | `subscription_service`, `kiwify_service`, `charges` |
| **Painel admin** | `admin_panel.py` | `admin_metrics_service`, `admin_dre_service`, `platform_usage_service` |
| **Diagnóstico IA** | `ai_diagnostics.py` | `ai_diagnostic_service`, `ai_credit_service`, `ai_snapshot_service` |
| **WhatsApp** | `whatsapp.py` | `whatsapp_resumo_service`, `evolution_client` |

## Regras de domínio

1. **Canal vem do clique, não do pedido.** A comissão por canal usa o canal
   real registrado nos cliques.
2. **Categoria é nível 1.** O filtro de categoria opera só no primeiro nível
   da hierarquia da Shopee.
3. **Campanha de conta de anúncio desmarcada não conta como ativa.** Nem
   campanha com orçamento vitalício esgotado — mas campanha **reativada**
   volta a contar (já foi bug nos dois sentidos).
4. **Sync da Shopee é upsert aditivo.** Sem `DELETE` de janela — janela
   incompleta da API apagava dado bom.
5. **Espelho Meta → AdSpend** grava gasto e cliques com `source` marcado, e
   guarda backup do valor manual antes de sobrescrever.
6. **As três datas não são a mesma**: data da venda ≠ data do gasto ≠ data da
   sincronização. Cruzamento é sempre pela data do fato.

## Planos

`app/core/plans.py` é a fonte única (espelhada em
`marketdash-frontend/src/shared/lib/plans.ts`). `essencial` / `pro` / `max`.
Limite **`-1` = ilimitado** (MAX) — e o frontend precisa entender isso também,
senão mostra "-1" na tela.

Automação de Instagram é **exclusiva do MAX**. Diagnóstico IA e WhatsApp
ficam **ocultos em produção** por `isProductionHost()` no frontend.

## Feature flags

`feature-flags.json` na **raiz do monorepo**, lido por
`app/core/feature_flags.py` (e montado como volume no compose). Backend e
frontend leem o mesmo arquivo — flag nova entra lá, não em dois lugares.
