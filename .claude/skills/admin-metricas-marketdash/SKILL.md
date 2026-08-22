---
name: "admin-metricas-marketdash"
description: "Métricas do painel admin do MarketDash: MRR, ARPU, churn, novas assinaturas, lista de clientes, aba Uso, despesas e DRE. Use ao mexer em admin_metrics_service, admin_dre_service, platform_usage_service ou charges, e sempre que um número do painel estiver 'estranho'."
---

# Métricas do painel admin — MarketDash

Aqui um erro não quebra teste: **muda o número que o dono usa para decidir**,
e sai plausível. Toda regra abaixo existe porque algum número já saiu errado.

## Onde mora

| Arquivo | Responde |
|---|---|
| `admin_metrics_service.py` | MRR, ARPU, churn, novas/canceladas, lista de clientes |
| `platform_usage_service.py` | Aba Uso — usuárias ativas, dias ativos, atividade por usuária |
| `admin_dre_service.py` | DRE |
| `charges.py` | Cobranças (fonte do faturamento) |
| `daily_access_service.py` | `record_access()` — dedupe de acesso por janela de 2min |

Rotas em `app/api/v1/routes/admin_panel.py`; telas em
`marketdash-frontend/src/features/admin/pages/`.

## As definições que não podem ser trocadas

### Populações

- **`renewing_subscribers()`** — não cancelada **e** com acesso vigente.
  Base de **MRR, ARPU, plano × periodicidade e do denominador do churn**.
- **`active_subscribers()`** — tem acesso, **mesmo cancelada**. Base da aba
  **Uso**, dos alertas e da lista de **Clientes**.

Cancelado-com-acesso não pode churnar de novo — foi por isso que o
denominador do churn migrou de `active` para `renewing`.

### MRR

- **Bruto** = `list_price_cents(plano, periodicidade)` de `app/core/plans.py`,
  dividido pelo período. **Não** o `amount_gross_cents` da última cobrança,
  que pode carregar cupom/desconto histórico.
- **Líquido** = valor real cobrado.
- Fallback para o valor real só quando o plano não está no catálogo.

### Faturamento

Sai de `charges.py`, **uma cobrança por `order_ref`**. O array
`Subscription.charges.completed` não é fonte — reintroduzi-lo duplica tudo
que veio do import histórico. (O faturamento já apareceu dobrado por isso.)

### Novas assinaturas

`new_subscriptions()` conta pela **1ª cobrança paga histórica** do assinante,
sem filtrar por status atual. Assinante cuja única cobrança do mês está
marcada `is_plan_change=True` **não pode sumir** da contagem.

### Aba Uso

`_base_ativa()` exclui quem **não tem `user_id`** (importado do histórico,
nunca criou conta). Qualquer outra função que filtre a mesma população
precisa fazer o mesmo — e checar o campo **bruto do evento** (`ev.user_id`),
não uma variável local que pode ter sido resolvida por fallback de e-mail.
Foi assim que o card "Sem acesso 10d+" e a lista divergiram (17 × 26).

## Fuso — o bug silencioso

**Nunca** `cast(coluna_timestamptz, Date)` em SQL: trunca no fuso da sessão
do Postgres, não em BRT. Uma janela de "7 dias" espalhou por **8 datas**
("Dias ativos: 8"). Bucketing por dia civil é em **Python**, com
`_brt_date()` sobre `datetime` já buscados. Vale para 7d/30d/90d, dias
ativos, fechamento de mês e qualquer gráfico por dia.

## `list_clients()` — frágil por natureza

A de-dup por upgrade depende de `is_plan_change`. Qualquer mudança nessa
lógica exige reconferir os 4 achados da Rodada 6 (zero-rows, contaminação de
total por CPF, candidatura efêmera). Todos têm teste de regressão sintético
em `tests/unit/test_admin_metrics_service.py`.

Filtros da lista (`q`, `status`, `plan`, alertas) valem também no
**export CSV** — já houve o caso do CSV exportando a base inteira enquanto a
tela mostrava o filtro.

## Antes de mergear

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest \
  tests/unit/test_admin_metrics_service.py \
  tests/unit/test_admin_metrics_service_churn.py \
  tests/unit/test_churn_denominador_renovando.py \
  tests/unit/test_charges_por_order_ref.py \
  tests/unit/test_atividade_usuaria_plano.py -v
```

Depois, acione o agent `admin-metrics-reviewer`.

**Número que mudou sem alguém pedir é achado**, mesmo que o novo esteja
certo: vai para o `CHANGELOG.md` da raiz, porque alguém vai comparar com o
print da semana passada.

## Validar contra dado real

Os scripts `scripts/validar_rodada6.py`, `validar_rodada7.py` e
`diagnostico_rodada7.py` existem para conferir números contra o banco.
**Rodar contra homologação não substitui produção** — a base é menor e
diferente, e vários aceites da Rodada 7 ficaram pendentes exatamente por
isso.
