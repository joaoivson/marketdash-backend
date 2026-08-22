---
description: Isolamento de dados por usuario, RLS e checagem de acesso por assinatura
globs: "app/{repositories,services,api}/**/*.py"
---

# Isolamento por usuário — a regra que não tem exceção

Cada cliente vê os dados de comissão, gasto e campanha **dele**. Vazamento
aqui não é bug de UX, é incidente.

## 1. Toda query filtra por `user_id`

```python
# certo
rows = db.query(DatasetRow).filter(DatasetRow.user_id == user_id).all()

# errado — mesmo que "só o admin chama"
rows = db.query(DatasetRow).all()
```

Vale para agregação, `count()`, export CSV e task do Celery. Task que roda
fora do request **não tem `current_user`** — ela recebe o `user_id` como
argumento e filtra por ele; nunca varre a tabela inteira.

## 2. RLS via `SET LOCAL app.current_user_id`

`get_current_user()` seta a variável na sessão do Postgres. É a **segunda**
linha de defesa, não a primeira — o filtro na query continua obrigatório.
`SET LOCAL` morre com a transação: se você abrir sessão nova (script, task),
seta de novo ou não conta com RLS.

## 3. Buscar por id sem checar dono é vazamento

```python
# errado — o id é sequencial, dá pra varrer
site = db.query(CaptureSite).filter(CaptureSite.id == site_id).first()

# certo
site = db.query(CaptureSite).filter(
    CaptureSite.id == site_id,
    CaptureSite.user_id == current_user.id,
).first()
```

Não encontrado → **404**, não 403. O 403 confirma que o recurso existe.

## 4. Acesso por assinatura: `subscription_has_access()`

```python
from app.services.subscription_service import subscription_has_access
```

**Nunca cheque `subscription.is_active` cru.** Assinatura cancelada na Kiwify
mantém acesso até `access_until` — o cancelamento é o último evento que a
Kiwify manda, então o acesso precisa cair sozinho por data.

Distinção que o painel admin depende:

- `renewing_subscribers()` — não cancelada e com acesso vigente → base de
  **MRR, ARPU, plano/periodicidade, denominador do churn**
- `active_subscribers()` — tem acesso, mesmo cancelada → base da aba **Uso**,
  alertas e lista de **Clientes**

Usar uma no lugar da outra muda número de negócio sem quebrar teste nenhum.

## 5. Quem tem `user_id` e quem não tem

Assinante importado do histórico Kiwify pode existir **sem conta criada**
(`user_id` nulo). Toda função que filtre essa população precisa concordar —
e checar o campo **bruto do evento** (`ev.user_id`), não uma variável local
que pode ter sido resolvida por fallback de e-mail. Foi assim que o card
"Sem acesso 10d+" e a lista filtrada pelo mesmo critério passaram a divergir.
