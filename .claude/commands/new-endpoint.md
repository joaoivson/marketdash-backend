---
name: new-endpoint
description: Criar um endpoint novo no backend MarketDash seguindo as camadas (schema, repository, service, rota, registro).
---

Crie um endpoint novo seguindo as camadas do projeto.

## Input esperado

O usuário descreve o endpoint (ex.: "listar campanhas ativas por conta de
anúncio com filtro de período").

Se não estiver claro **quem pode ver o dado** e **qual o filtro de usuário**,
pergunte antes de escrever qualquer linha.

## Passos

### 1. Achar o módulo certo

Liste `app/api/v1/routes/` e escolha o módulo existente. Só crie módulo novo
se for domínio realmente novo — e então ele precisa ser registrado.

### 2. Schema Pydantic

Em `app/schemas/`. Request e response, ambos tipados. Nada de `dict` solto
atravessando a fronteira HTTP.

### 3. Repository

Em `app/repositories/`. A query, com **filtro por `user_id`**. Se filtra por
coluna nova, confirme que existe índice.

### 4. Service

Em `app/services/`. A regra de negócio. Se precisa checar assinatura, use
`subscription_has_access()` — nunca `is_active` cru.

### 5. Rota

Em `app/api/v1/routes/`. Fina: valida, chama **um** service, devolve.

- `current_user` via `Depends(get_current_user)`
- Não encontrado (inclusive recurso de outro dono) → **404**, não 403
- **Não use `user_id` como nome de query param** — o frontend injeta
  `?user_id=user_N` em toda request e sobrescreveria o seu

### 6. Registrar

Adicione o import e o `include_router` em `app/api/v1/routes/__init__.py`.
**Pular este passo é a causa nº 1 de "criei o endpoint e dá 404".**

### 7. Teste

`tests/unit/test_<assunto>.py`, nomeando
`test_{ação}_{cenário}_{resultado_esperado}`. Inclua o caso de **acesso de
outro usuário → 404**.

### 8. Verificar

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
docker-compose up -d app
curl -s localhost:8000/docs > /dev/null && echo "app no ar"
```

### 9. Fechar

Se o endpoint muda algo visível ao usuário, atualize o `CHANGELOG.md` da
raiz. Registre decisão ou pendência em `.claude/memoria/DECISOES.md` e a
entrada do dia em `DIARIO.md`.
