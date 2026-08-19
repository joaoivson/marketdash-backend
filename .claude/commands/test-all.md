---
name: test-all
description: Rodar a bateria de verificacao do backend MarketDash (testes, subida do app e checagem de fila) e reportar a saida real.
---

Rode a verificação completa do backend e **mostre a saída** — não afirme que
passou sem evidência.

## 1. Testes unitários

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
```

> O `pytest tests/ -v` do `CLAUDE.md` **não funciona**: o venv default é
> Python 3.9 e quebra na coleção. Use o `.venv312`.

Para um arquivo:

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_admin_metrics_service.py -v
```

## 2. Subir os serviços

```bash
docker-compose up -d
docker-compose ps
```

Backend roda em **Docker**, nunca direto no host. `app` na porta **8000**.

## 3. App responde

```bash
curl -s localhost:8000/ | head -5
```

## 4. Worker registrou as tasks

```bash
docker-compose logs worker --tail 30
```

Procure por `unregistered task` — significa módulo fora de
`celery_app.conf.include`. Confirme também que a fila no log é
`marketdash-<ref>` e **não** `celery`: fila default significa que
`_fila_do_banco()` não pegou o `DATABASE_URL`.

## 5. Se mexeu em métrica do painel admin

Rode também os testes de regressão que guardam os achados das rodadas 6 e 7:

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest \
  tests/unit/test_admin_metrics_service.py \
  tests/unit/test_admin_metrics_service_churn.py \
  tests/unit/test_churn_denominador_renovando.py \
  tests/unit/test_charges_por_order_ref.py -v
```

## 6. Reportar

Diga o que passou, o que falhou e **cole a saída relevante**. Teste pulado é
teste que não passou — diga isso explicitamente.
