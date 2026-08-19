---
description: Convencoes de codigo Python no backend MarketDash
globs: "app/**/*.py"
---

# Code Style — Backend

## Nomes

| Coisa | Forma |
|---|---|
| Arquivo | `snake_case.py` |
| Classe | `PascalCase` |
| Função / variável | `snake_case` |
| Constante | `UPPER_SNAKE_CASE` |
| Teste | `test_{ação}_{cenário}_{resultado_esperado}` |

## Sempre

- **Type hints** em toda assinatura pública.
- **Schema Pydantic** para todo request e response — nada de `dict` solto
  atravessando a fronteira HTTP.
- `logger = logging.getLogger(__name__)` no topo do módulo.
- `HTTPException` com o status certo: 404 para não-encontrado (inclusive
  recurso de outro dono), 403 para sem permissão, 422 o FastAPI já dá.
- Comentário explica **por quê**, não o quê. O código já diz o quê.
- Português nos comentários e nas mensagens de erro visíveis ao usuário.

## Nunca

- `except Exception: pass` — engolir exceção em task é como o bug de fila
  ficou invisível por semanas. Logue e grave estado terminal.
- `print()` em código commitado — use `logger`.
- Query dentro de loop (N+1). Traga em lote no repository.
- Credencial em código, em `docker-compose.yml` versionado, ou em
  `settings.local.json`. Vai para `.env` / gerenciador de segredo.

## Dinheiro

Comissão, receita e gasto são valores financeiros que a usuária confere
contra o relatório da Shopee. Some com cuidado com float; ao expor em JSON,
mantenha a precisão que o CSV trouxe.

**As colunas `cost` e `profit` de `dataset_rows_v2` estão mortas** — não são
fonte de nada. O KPI que a usuária vê é calculado no frontend a partir de
`raw_data`. Ler dessas colunas dá número errado com cara de certo.

## Formatação

`black` roda automaticamente no `PostToolUse` (ver `settings.json`). Não
gaste tempo alinhando à mão.

## Testes

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
```

O `pytest tests/ -v` do `CLAUDE.md` **não funciona** — o venv default é 3.9 e
quebra na coleção. Teste novo vai em `tests/unit/`; fixture de banco em
`conftest.py`; Supabase Auth é mockado no unitário.
