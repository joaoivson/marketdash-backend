---
description: Fuso horario, corte de periodo e bucketing por dia civil (BRT)
globs: "app/{services,repositories}/**/*.py"
---

# Fuso — o erro que passa despercebido porque o número sai plausível

O produto é brasileiro. **Dia civil é BRT**, não UTC, não o fuso da sessão do
Postgres.

## Nunca agrupe por dia com `cast(coluna, Date)` em SQL

```python
# errado — trunca no fuso da SESSÃO do Postgres
func.cast(UserLogin.logged_at, Date)
```

`cast` de `timestamptz` para `date` usa o `TimeZone` da sessão. Uma janela de
"7 dias" chegou a espalhar por **8 datas diferentes** — foi o "Dias ativos:
8" reportado pelo Luiz.

## Faça o bucketing em Python, com `_brt_date()`

O helper já existe em `app/services/admin_metrics_service.py` e é usado por
`platform_usage_service.py`. Busque os `datetime` e converta em Python:

```python
from app.services.admin_metrics_service import _brt_date, BRT

dia = _brt_date(evento.logged_at)   # date civil em BRT
```

Vale para 7d/30d/90d, "dias ativos", fechamento de mês, gráfico por dia e
qualquer contagem que responda "quantos por dia".

## Corte de período no frontend também é BRT

Os atalhos (Ontem / 7d / 14d / mês) cortam no **fim do dia anterior em
Brasília** — helpers `*BR` em `marketdash-frontend/src/shared/lib/date.ts`.
Se o backend passar a cortar em UTC, os dois divergem à noite (entre 21h e
0h BRT o dia UTC já virou) e o usuário vê números diferentes na mesma tela.

## As três datas não são a mesma

- **Data da venda / comissão** — quando o pedido aconteceu
- **Data do gasto (`AdSpend.date`)** — a que o usuário lança
- **Data de sincronização** — quando o dado entrou aqui

Cálculo de ROAS e lucro casa **venda × gasto pela data do fato**, nunca pela
data de sincronização.
