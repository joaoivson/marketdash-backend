---
description: Arquitetura em camadas do backend MarketDash — routes, services, repositories, models
globs: "app/**/*.py"
---

# Camadas — não pule nenhuma

```
api/v1/routes/  → services/  → repositories/  → models/
   (HTTP)         (regra)       (query)          (ORM)
```

## O que cada camada pode fazer

| Camada | Pode | Não pode |
|---|---|---|
| `routes/` | Validar entrada (Pydantic), chamar **um** service, montar a resposta HTTP | Query SQLAlchemy, `if` de regra de negócio, cálculo |
| `services/` | Regra de negócio, orquestrar repositories, disparar Celery | `db.query(...)` direto, `HTTPException` de validação de payload |
| `repositories/` | Query, filtro, agregação, upsert | Regra de negócio, decidir o que o usuário pode ver |
| `models/` | Colunas, relacionamentos, `__tablename__` | Query, regra |

Rota que faz `db.query(Model)` é bug de arquitetura, mesmo funcionando.

## Endpoint novo — a ordem

1. Schema Pydantic em `schemas/`
2. Método no repository em `repositories/`
3. Método no service em `services/`
4. Rota em `api/v1/routes/`
5. Registrar o import em `api/v1/routes/__init__.py` — **esquecer isso é a
   causa mais comum de "criei o endpoint e dá 404"**

## Fora do `/api/v1`

Só existem três exceções, todas por motivo externo — não crie a quarta sem
registrar em `memoria/DECISOES.md`:

- `/cakto/webhook` — compatibilidade com URL já cadastrada no provedor
- `/webhooks/instagram` — URL cadastrada no painel da Meta, não versiona
- `/c/{slug}/og` — HTML com meta tags para crawler de rede social

## Nome de query param

**Nunca use `user_id` como nome de query param em endpoint novo.** O
`fetchWithAuth` do frontend injeta `?user_id=user_N` em toda request por
compatibilidade — o seu parâmetro receberia esse valor, no formato errado,
sem erro nenhum. Use `owner_id`, `target_user`, ou pegue do
`current_user` (que é o certo na maioria dos casos).
