---
name: "assinatura-kiwify-marketdash"
description: "Assinatura, planos e acesso no MarketDash: webhook Kiwify, cobranças, cancelamento com acesso, gating por plano (essencial/pro/max) e o provider legado Cakto. Use ao mexer em subscription_service, kiwify_service, charges, plans.py, ou ao investigar 'por que esse cliente perdeu/ganhou acesso'."
---

# Assinatura, planos e acesso — MarketDash

## Quem manda no acesso

```python
from app.services.subscription_service import subscription_has_access
```

**Nunca cheque `subscription.is_active` cru.** Cancelamento na Kiwify mantém
o acesso até `access_until` — o cancelamento é o **último** evento que a
Kiwify envia, então o acesso precisa cair sozinho por data, sem depender de
webhook novo.

`subscription_has_access()` lê, em ordem: `assinatura_vence_em`,
`expires_at`, `provider_due_date`, `cakto_due_date`. Cancelada **sem** data
conhecida respeita o `is_active` do webhook.

## As duas populações — não são a mesma pergunta

| Função | Quem entra | Responde |
|---|---|---|
| `renewing_subscribers()` | Não cancelada **e** com acesso | "quem me paga no mês que vem" → MRR, ARPU, plano × periodicidade, **denominador do churn** |
| `active_subscribers()` | Tem acesso, **mesmo cancelada** | "quem consegue entrar hoje" → aba Uso, alertas, lista de Clientes |

Trocar uma pela outra muda número de negócio sem quebrar teste nenhum.

## Cobrança

**Uma cobrança = um evento pago, chaveado por `order_ref`**
(`app/services/charges.py`).

O array `Subscription.charges.completed` **não é fonte de cobrança**. Ele
serve só para detectar webhook perdido (`unknown_array_charges`).
Reintroduzi-lo como fonte volta a duplicar tudo que veio do import
histórico da Kiwify.

## O plano vem do `checkout_link`

**O `product_id` da Kiwify é o mesmo para todos os planos.** Derivar o plano
dele classifica a base inteira igual. O que distingue é o link de checkout
(um por plano × periodicidade).

Outro buraco já corrigido: o webhook chegava a **descartar compra paga antes
de criar o usuário** — pagamento sem conta é caso real, não erro.

## Catálogo de planos

`app/core/plans.py` é a fonte única, espelhada em
`marketdash-frontend/src/shared/lib/plans.ts`.

| Plano | Menus extras | Limites (páginas / links / créditos IA) |
|---|---|---|
| `essencial` | — | 0 / 0 / 0 |
| `pro` | captura, meus_links, diagnóstico IA | 15 / 30 / 200 |
| `max` | tudo do Pro + **automações (Instagram)** | **-1 / -1** / 1000 |

**`-1` significa ilimitado** — e o frontend precisa entender isso, senão
mostra "-1" na tela. `max` ainda está fora da página de vendas: entra só por
link direto da Kiwify.

Adicionar plano = **uma entrada aqui** + a espelhada no frontend.

## Import histórico

Assinante importado do CSV histórico da Kiwify pode existir **sem
`user_id`** (nunca criou conta). Toda população derivada precisa concordar
em incluí-lo ou não — e o guard checa o campo **bruto do evento**
(`ev.user_id`), nunca uma variável local resolvida por fallback de e-mail.

Armadilha já vivida: **CPF sem zero à esquerda** no CSV duplicou identidades.
Ao casar registro por documento, normalize o zero à esquerda.

## Cakto

Provider legado. A rota `/cakto/webhook` existe fora do `/api/v1` por
compatibilidade com URL já cadastrada. Não é o caminho de assinatura nova.
