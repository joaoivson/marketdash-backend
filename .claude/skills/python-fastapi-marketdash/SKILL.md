---
name: "python-fastapi-marketdash"
description: "Padrões de código Python/FastAPI/SQLAlchemy neste repo: dependências, sessão de banco, erros, logging, configuração, criptografia de credencial, cache e testes. Use ao escrever ou revisar qualquer código Python do backend MarketDash, e ao decidir 'como se faz isso aqui'."
---

# Python / FastAPI — MarketDash

## Configuração

`app/core/config.py` — `pydantic-settings` lendo `.env`. Toda variável nova
entra ali com tipo e default explícito. Variável sem default que falta no
ambiente derruba o boot; com default vazio, degrada em silêncio (é o caso de
`FACEBOOK_APP_ID`: vazio faz `/facebook` devolver 503).

## Dependências (`app/api/v1/dependencies.py`)

- `get_db()` — sessão SQLAlchemy por request
- `get_current_user()` — valida o token chamando
  `supabase.auth.get_user(token)` (**não** decodifica JWT local), busca o
  usuário local **por e-mail** e seta `app.current_user_id` para RLS
- `get_supabase_client()` / `get_supabase_service_client()` — inicialização
  preguiçosa; o service client **contorna RLS**, use só onde é necessário
  (Storage) e nunca para ler dado de usuário

Nota de auth que já custou tempo: **`/register` não cria usuário no Supabase**
— quem cria é o `/login` (migração preguiçosa). Fluxo de teste que passa só
pelo register não gera conta válida.

## Erros

`app/core/errors.py` registra os handlers globais. Na rota, `HTTPException`
com o status certo:

| Situação | Status |
|---|---|
| Recurso não existe **ou é de outro dono** | 404 |
| Sem permissão / assinatura sem acesso | 403 |
| Payload inválido | 422 (o FastAPI já dá) |
| Integração externa não configurada | 503 |

404 para recurso de outro dono é deliberado: 403 confirmaria que ele existe.

## Logging

```python
logger = logging.getLogger(__name__)
```

`app/core/logging.py` configura no boot. `print()` não vai para o agregador.
Em task do Celery, log de início/fim com o id do registro é obrigatório —
sem ele a falha é indistinguível de "nunca rodou".

## Credenciais de integração

`app/core/encryption.py` cifra token de terceiro (Shopee, Facebook,
Instagram) antes de gravar. `SHOPEE_ENCRYPTION_KEY` vem do ambiente. **Token
de terceiro nunca vai para o banco em claro** e nunca aparece em log — nem
truncado, nem em mensagem de erro.

## Cache

`app/core/cache.py`. Chave de cache de dado de usuário **inclui o
`user_id`** — cache global de dado escopado é vazamento entre clientes com
outra roupa.

## Testes

```bash
PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
```

- `tests/unit/` — Supabase Auth mockado
- `tests/performance/`, `tests/load/` — cenários pesados
- Nome: `test_{ação}_{cenário}_{resultado_esperado}`
- Bug corrigido ganha teste que **falha antes do fix** — vários testes deste
  repo existem exatamente para segurar achado de rodada anterior
  (`test_churn_denominador_renovando`, `test_charges_por_order_ref`,
  `test_campaign_active_count_orcamento_esgotado`)
- **Mexeu em schema Pydantic? Teste a ROTA, não só o service.** A suíte é quase
  toda no nível de service, e isso dá uma sensação de cobertura que a camada de
  rota não tem. Em 04/09, tirar um campo de `CampanhaCriar` deixou a rota lendo
  `payload.descricao`: o Pydantic v2 **não guarda campo que não declarou**,
  então é `AttributeError` — que não é `ValueError` nem exceção de domínio,
  nenhum `except` da rota pega, e o handler global devolve **500**. Criar
  campanha ficou inoperante com **1081 testes verdes**. Chamar a função da rota
  direto no teste (passando `current_user` e `db`) já teria pego.
- **Postgres local (`:5434`) ausente = arquivo inteiro PULA, em verde.** Vários
  arquivos têm `pytest.mark.skipif` no topo. "Tudo passou" sem o
  `docker compose up db` pode significar "quase nada rodou" — confira a
  contagem, não só a cor.

## Estilo

`black` roda no `PostToolUse`. Type hints em toda assinatura pública.
Comentário explica **por quê** — e neste repo os comentários longos do
`celery_app.py` são exemplo do padrão: eles registram o incidente que a linha
de código previne.
