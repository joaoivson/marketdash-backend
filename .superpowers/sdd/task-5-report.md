# Task 5: Upgrade em qualquer ordem (caso Ana Ariel) — Report

## O que foi implementado

1. **`app/services/subscription_event_recorder.py`**: substituída integralmente a função `_mark_plan_change_if_needed` (retornava `bool`) por `encontrar_par_de_plan_change` (retorna o `SubscriptionEvent` parceiro ou `None`). A diferença central: a busca agora ocorre nos **dois sentidos**.
   - Se o evento chegando é pago (`order_approved`, `subscription_renewed`, `compra_aprovada`), procura um `subscription_canceled` do mesmo CPF.
   - Se o evento chegando é `subscription_canceled`, procura um evento pago do mesmo CPF.
   - A janela de busca no banco é `agora - 30d` até `agora + 30d` (antes só olhava pra trás).
   - O gap usado na decisão é `abs(agora - recebido)` (antes era `agora - recebido`, sem valor absoluto — não fazia sentido pra busca bidirecional).
   - Regra de decisão inalterada: mesmo plano em ≤1 dia = continuação; plano diferente em ≤30 dias = upgrade/downgrade.
   - Adicionada constante `PAID_LIKE_EVENTS` (antes era uma tupla inline dentro da função).

2. **`record_subscription_event`**: troca de chamada — de `is_plan_change = _mark_plan_change_if_needed(db, fields)` + bloco de busca manual do cancelamento recente, para:
   ```python
   par = encontrar_par_de_plan_change(db, fields)
   is_plan_change = par is not None
   if par is not None and not par.is_plan_change:
       par.is_plan_change = True
   ```
   Mais simples porque a nova função já retorna o objeto do par (não precisa re-buscar).

3. **`tests/unit/test_plan_change_qualquer_ordem.py`** (novo): 6 testes cobrindo o caso Ana Ariel (cancelamento chega depois da nova assinatura), o caso antigo (assinatura chega depois do cancelamento), continuação de mesmo plano, e os dois limites negativos (mesmo plano fora de 1 dia, plano diferente fora de 30 dias) — mais o caso sem CPF.

4. **`tests/unit/test_subscription_event_recorder.py`**: os 6 testes que chamavam `_mark_plan_change_if_needed` foram reescritos para `encontrar_par_de_plan_change`, trocando `is True`/`is False` por `is not None`/`is None` (com asserção adicional do CPF do evento retornado em dois deles, pra garantir que o objeto certo veio de volta, não só "algo").

5. **`scripts/backfill_plan_changes.py`** (novo): script one-off que varre `subscription_events` com `customer_cpf` não nulo e `is_plan_change=False`, reaplica `encontrar_par_de_plan_change` ancorado em `received_at` de cada evento histórico, e marca os pares que a regra antiga não pegou. Suporta `--dry-run` (rollback, não commita). **Não foi executado** — nem com `--dry-run` — conforme instrução explícita da task.

## Testes e resultados

**TDD — RED:**
```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_plan_change_qualquer_ordem.py -v
...
ImportError: cannot import name 'encontrar_par_de_plan_change' from 'app.services.subscription_event_recorder'
Interrupted: 1 error during collection
```

**TDD — GREEN** (depois de implementar `encontrar_par_de_plan_change` e ligar em `record_subscription_event`):
```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_plan_change_qualquer_ordem.py tests/unit/test_subscription_event_recorder.py -v
...
21 passed in 0.35s
```
Todos os 6 testes novos passaram, e todos os 15 testes pré-existentes em `test_subscription_event_recorder.py` (agora usando `encontrar_par_de_plan_change`) passaram.

**Suíte unitária completa:**
```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/ -v
...
3 failed, 446 passed, 18 warnings in 2.28s
```
As 3 falhas são em `tests/unit/test_shopee_upsert_additive.py` (AppID Shopee inválido) — **pré-existentes e não relacionadas** a esta task. Confirmado via `git stash` + rodar a suíte na branch antes das minhas mudanças: as mesmas 3 falhas já existiam (3 failed, 3 passed, 2.28s → mesmo padrão). Nenhum teste que eu toquei ou que envolve `subscription_event_recorder.py` falha.

**Backfill — verificação de sintaxe (sem tocar no banco):**
```
$ .venv312/bin/python -c "import ast; ast.parse(open('scripts/backfill_plan_changes.py').read())"
SYNTAX_OK

$ PYTHONPATH=$PWD .venv312/bin/python scripts/backfill_plan_changes.py --help
usage: backfill_plan_changes.py [-h] [--dry-run]
...
```
`--help` do argparse retorna antes de qualquer `SessionLocal()`/query — nenhuma conexão de banco foi aberta.

## Arquivos alterados

- `app/services/subscription_event_recorder.py` (modificado — substituição da função + chamada)
- `tests/unit/test_plan_change_qualquer_ordem.py` (novo)
- `tests/unit/test_subscription_event_recorder.py` (modificado — 6 testes reescritos)
- `scripts/backfill_plan_changes.py` (novo, não executado)

## Grep de confirmação

```
$ grep -rn "_mark_plan_change_if_needed" --include="*.py" .
(sem resultados)
```
Nenhuma referência à função antiga sobra em código Python (a única menção restante no repo é dentro do plano `docs/superpowers/plans/2026-08-11-painel-admin-rodada6.md`, que é documentação do plano, não código).

## Achados da self-review

- **Fronteiras da janela (1 dia / 30 dias)**: conferidas linha a linha. `gap <= timedelta(days=1)` e `gap <= janela` (30 dias) são ambos inclusivos (`<=`), consistente com a redação "≤1 dia" / "≤30 dias" do docstring — sem off-by-one. O teste `test_mesmo_plano_depois_de_uma_semana_nao_e_par` (gap de 7 dias, mesmo plano) cai fora do ramo "mesmo plano ≤1 dia" e não bate no ramo "plano diferente" (porque é o mesmo plano) → retorna `None`, correto. O teste `test_plano_diferente_depois_de_40_dias_nao_e_par` (gap de 40 dias) nem chega a ser candidato: a query já filtra `received_at` para dentro de `agora ± 30 dias`, então o evento de 40 dias atrás é excluído antes mesmo do loop — também correto, e reforça que não há dependência de o loop cortar certo (a query já corta).
- **`abs()` no gap**: a função antiga calculava `gap = agora - recebido` sem valor absoluto (fazia sentido porque só buscava pra trás). Troquei para `abs(agora - recebido)`, exigido pela busca bidirecional — confirmado pelo teste `test_cancelamento_depois_da_nova_assinatura_forma_par`, onde o evento candidato (Pro) está 1 minuto no "futuro" relativo ao `agora` do teste (BASE+19min vs threshold BASE+20min), então sem `abs()` o gap teria dado negativo e o `<=` teria passado por acidente mesmo se a lógica estivesse errada — validei manualmente que com `abs()` o comportamento é o mesmo produzido corretamente pro caso positivo também.
- **Escopo respeitado**: não toquei em `alertar_cobrancas_desconhecidas` nem em nenhuma outra função do arquivo. `git diff` confirma que só as linhas da função substituída e da chamada em `record_subscription_event` mudaram.
- **Diff bate com o brief**: comparei a implementação final com o código-fonte do brief (Step 3 e Step 4) — idêntica, sem desvios.

## Preocupações

- Nenhuma bloqueante. Os números de aceite (14 novas / 4 churn em agosto) dependem do backfill rodar em produção — como instruído, não rodei o backfill (nem `--dry-run`); isso fica para a Task 14.
- As 3 falhas pré-existentes em `test_shopee_upsert_additive.py` não foram tocadas nem investigadas a fundo (fora do escopo desta task), apenas confirmadas como pré-existentes via `git stash`.

## Fix de review — direção reversa sem cobertura em sessão real

**Finding (Important):** os 5 testes DB-backed pré-existentes em `test_subscription_event_recorder.py` só exercitavam "evento pago chega → procura cancelamento". A direção reversa (o caso real da Ana Ariel: "cancelamento chega → procura evento pago") só era testada em `test_plan_change_qualquer_ordem.py`, inteiramente via `MagicMock` — uma cadeia de mock que ignora os argumentos passados a `.filter(...)`. Um bug de copy-paste que trocasse a lista de `event_type` buscada (ex.: `procurado = ["subscription_canceled"]` em vez de `PAID_LIKE_EVENTS` no ramo reverso) não seria pego por nenhum teste.

**Fix:** adicionados 2 testes em `tests/unit/test_subscription_event_recorder.py`, reaproveitando a fixture `db` (SQLite real) já usada pelos outros testes de `encontrar_par_de_plan_change` neste arquivo — nenhuma fixture nova foi inventada:

- `test_cancelamento_chegando_encontra_pagamento_anterior_sessao_real`: insere um `SubscriptionEvent` real (`order_approved`, plano "Pro", CPF "666"). Chama `encontrar_par_de_plan_change` com `fields` de um `subscription_canceled` chegando para o plano "Essencial", mesmo CPF, `reference_time` 8 minutos depois do pagamento (timeline exata da Ana Ariel). Assert: retorna o mesmo objeto (`par.id == pago.id`), não `None`, não outro row.
- `test_cancelamento_chegando_sem_pagamento_nao_forma_par_com_outro_cancelamento`: insere só um OUTRO `subscription_canceled` (mesmo CPF, sem nenhum evento pago) e confirma que a mesma busca reversa retorna `None` — pega exatamente a classe de bug "busca a lista de event_type errada".

Novo helper `_paid(db, cpf, received_at, ...)` no mesmo arquivo, espelhando o `_cancel(...)` já existente.

**Verificação de que os testes realmente pegam a regressão:** injetei temporariamente o bug descrito (`procurado = list(PAID_LIKE_EVENTS)` → `procurado = ["subscription_canceled"]` no ramo `evento == "subscription_canceled"`), rodei os 2 testes novos — ambos falharam (`assert None is not None` / assert do par errado) — depois revertido com `git checkout -- app/services/subscription_event_recorder.py` (confirmado `git diff --stat` vazio nesse arquivo antes de commitar). Nenhuma mudança permanece em `subscription_event_recorder.py`.

**Resultado:**
```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_plan_change_qualquer_ordem.py tests/unit/test_subscription_event_recorder.py -v
...
23 passed in 0.31s
```
6 passed em `test_plan_change_qualquer_ordem.py` (inalterado) + 17 passed em `test_subscription_event_recorder.py` (15 pré-existentes + 2 novos).

Não executei `scripts/backfill_plan_changes.py` em nenhum momento. Não toquei em `encontrar_par_de_plan_change`, `charges.py`, `admin_metrics_service.py` ou no próprio script de backfill — só adicionei testes.
