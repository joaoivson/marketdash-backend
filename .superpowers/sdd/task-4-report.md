# Task 4 — Report: Cancelada sai do MRR e do card de ativos

## O que foi implementado

Em `app/services/admin_metrics_service.py`:

1. **`_is_canceled(ev) -> bool`** (função de módulo, logo após `_is_active_now`): reconhece cancelamento por `event_type == "subscription_canceled"` ou `subscription_status in ("canceled", "cancelled")`.

2. **`cancel_instants(events) -> Dict[str, List[datetime]]`** (função de módulo): reconstrói, por assinante (`_subscriber_key`), a lista de instantes de cancelamento REAL — ignora eventos com `is_plan_change=True` (upgrade/downgrade não é saída de cliente) e eventos com `cancel_reason` em `PRODUTOR_ADJUSTMENT_REASONS` (ajuste administrativo do produtor). Usa `canceled_at` quando existe, senão `received_at`.

3. **`AdminMetricsService.renewing_subscribers(as_of=None)`**: `[ev for ev in self.active_subscribers(as_of) if not _is_canceled(ev)]` — quem tem acesso E não está cancelada.

4. **`mrr_cents`**: passou a usar `renewing_subscribers()` como default (em vez de `active_subscribers()`) quando `actives` não é passado explicitamente.

5. **`mrr_at`**: ganhou o parâmetro `cancelamentos: Optional[Dict[str, List[datetime]]] = None`. Quando `None`, calcula via `cancel_instants(self._all_events())`. Para cada assinante, além de achar o período de cobertura (`cobrindo`) que contém `momento`, verifica se algum cancelamento caiu **dentro daquele período específico** (`cobrindo["inicio"] <= c <= momento`) — se sim, zera a contribuição daquele assinante para o MRR naquele instante.

6. **`series_12m`**: computa `cancelamentos = cancel_instants(all_events)` uma vez e passa para cada chamada de `mrr_at(momento, periodos, cancelamentos)`.

7. **`plan_frequency_distribution`** e **`dashboard`**: trocaram `self.active_subscribers()` por `self.renewing_subscribers()`.

8. **`alerts`** e **`list_clients`** e **`churn_for_month`**: mantidos com `self.active_subscribers()` — não fazem parte do escopo do brief (ver seção "Grep de `active_subscribers()`" abaixo).

## Evidência de TDD

**RED** — teste criado (`tests/unit/test_mrr_cancelado_sai.py`, conteúdo exatamente conforme o brief) rodado antes de qualquer mudança em produção:

```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_mrr_cancelado_sai.py -v
...
ImportError: cannot import name '_is_canceled' from 'app.services.admin_metrics_service'
```

**GREEN** — após implementar `_is_canceled`, `cancel_instants`, `renewing_subscribers` e o novo `mrr_at`:

```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit/test_mrr_cancelado_sai.py tests/unit/test_mrr_historico.py tests/unit/test_subscription_canceled_with_access.py tests/unit/test_platform_usage_base_ativa.py -v
...
42 passed, 14 warnings in 1.75s
```

Todos os 5 testes de `test_mrr_cancelado_sai.py` (`test_is_canceled_reconhece_evento_e_status`, `test_cancel_instants_ignora_plan_change_e_ajuste_do_produtor`, `test_renewing_exclui_cancelada_com_acesso`, `test_mrr_at_zera_no_mes_do_cancelamento`, `test_cancelamento_anterior_ao_periodo_nao_derruba_reassinatura`) passaram.

## Suíte completa

```
$ PYTHONPATH=$PWD .venv312/bin/python -m pytest tests/unit -q
3 failed, 440 passed, 18 warnings in 2.51s
```

As 3 falhas são em `tests/unit/test_shopee_upsert_additive.py` (`test_sync_commissions_never_deletes_the_window`, `test_sync_commissions_flags_suspected_partial_without_blocking_write`, `test_guard_ignores_first_day_of_window`) — **pré-existentes e não relacionadas a esta task**. Confirmei via `git stash` que as mesmas 3 falham igual no baseline antes de qualquer mudança minha (erro `ShopeePermanentError: AppID Shopee inválido`, algo de configuração de fixture/dado que já estava quebrado).

## Achados durante a implementação (fora do escopo estrito do brief)

O código do brief para `mrr_at` (passo 4) usa, como fallback quando `cancelamentos is None`, `cancel_instants(self._all_events())` — que exige `self.db`. Isso quebrou dois testes pré-existentes que criam `AdminMetricsService` **sem DB** (`AdminMetricsService.__new__(AdminMetricsService)` ou stub monkeypatchado) e chamam `mrr_at` sem passar `cancelamentos`:

1. **`tests/unit/test_mrr_historico.py`** — helper `_mrr()` chamava `svc.mrr_at(momento, periodos)` (sem `db`, sem `cancelamentos`). Corrigido passando `cancelamentos={}` explicitamente. Esse arquivo testa a reconstrução de vigência (`build_coverage_periods`), não a semântica de cancelamento — isolá-lo de `cancel_instants` preserva todos os valores numéricos das 12 asserções pré-existentes (incluindo o caso do Deivit, cancelado-com-acesso, que continua contando no MRR *dentro deste arquivo* porque ele não testa esse comportamento — quem testa é o novo `test_mrr_cancelado_sai.py`).

2. **`tests/unit/test_admin_metrics_service.py::test_mrr_series_includes_current_month_when_first_event_is_now`** — monkeypatch de `svc.mrr_at` com lambda de assinatura `(momento, periodos=None)`, que não aceita o novo terceiro argumento posicional que `series_12m` agora passa. Corrigido ampliando a assinatura do lambda para `(momento, periodos=None, cancelamentos=None)` — sem mudar nenhuma asserção.

Ambos os ajustes são só de "test double"/fixture (adaptar assinatura ao novo parâmetro), não mudam nenhum valor esperado nem lógica de negócio testada. Fiz isso porque a task pede explicitamente "rode a suíte unitária inteira uma vez antes de commitar" e esperava-se ausência de regressões — sem esse ajuste, dois testes que passavam no baseline quebrariam.

Isso é uma pequena divergência do texto "Esta task modifica só `app/services/admin_metrics_service.py` ... e cria `tests/unit/test_mrr_cancelado_sai.py`" — precisei também tocar `tests/unit/test_mrr_historico.py` e `tests/unit/test_admin_metrics_service.py`, mas apenas para corrigir assinaturas de test doubles quebradas pela mudança de contrato de `mrr_at` (a própria mudança de assinatura pedida pelo brief), não para alterar comportamento.

## Grep de `active_subscribers()` — o que troquei e o que mantive

```
346:    def active_subscribers(self, as_of: Optional[date] = None) -> List[SubscriptionEvent]:
351:    def renewing_subscribers(self, as_of: Optional[date] = None) -> List[SubscriptionEvent]:
359:        return [ev for ev in self.active_subscribers(as_of) if not _is_canceled(ev)]
362:        actives = actives if actives is not None else self.renewing_subscribers()   # mrr_cents — TROCADO (brief)
483:        start_actives = self.active_subscribers(as_of=...)                          # churn_for_month — MANTIDO
581:        actives = self.active_subscribers()                                          # alerts — MANTIDO (brief explícito)
702:        actives = self.renewing_subscribers()                                        # plan_frequency_distribution — TROCADO (brief)
730:        actives = self.renewing_subscribers()                                        # dashboard — TROCADO (brief)
802:        actives_map = {... for e in self.active_subscribers()}                       # list_clients — MANTIDO
```

- **Trocados** (conforme brief): `mrr_cents`, `plan_frequency_distribution`, `dashboard`.
- **Mantidos em `active_subscribers()`** (conforme brief, para `alerts`; por dedução para os demais, não listados no brief):
  - `alerts()` — o brief é explícito: "manter self.active_subscribers()".
  - `churn_for_month()` — usa `active_subscribers(as_of=...)` como denominador de "ativos no início do mês" para taxa de churn. Não está no escopo do brief (que só lista `mrr_cents`, `mrr_at`, `dashboard`, `plan_frequency_distribution`). Churn é sobre perda de ACESSO, não sobre deixar de "renovar" — manter parece semanticamente correto, mas não foi confirmado explicitamente no brief.
  - `list_clients()` — usa `active_subscribers()` só para decidir o `status` de exibição de cada linha (`ativo` vs `cancelado_com_acesso` vs `inativo`), não para MRR. Não está no escopo do brief. Manter é semanticamente correto (é sobre acesso, não sobre receita esperada).

Nenhuma chamada foi trocada por engano; nenhuma chamada listada no brief para trocar ficou esquecida.

## Arquivos alterados

- `app/services/admin_metrics_service.py` — implementação (ver brief, seguido à risca).
- `tests/unit/test_mrr_cancelado_sai.py` — novo, conteúdo exato do brief.
- `tests/unit/test_mrr_historico.py` — 1 linha ajustada (`_mrr` helper passa `cancelamentos={}`).
- `tests/unit/test_admin_metrics_service.py` — 1 lambda ajustado (assinatura do stub de `mrr_at`).

## Preocupações

1. **Divergência de escopo de arquivos** (documentada acima): toquei em dois arquivos de teste pré-existentes além do que o "Code Organization" da task autorizava explicitamente, para não deixar regressões na suíte completa. A mudança é puramente de assinatura de test double, sem alterar nenhuma asserção de valor — mas é uma divergência que vale confirmação humana.
2. **`churn_for_month` e `list_clients`** continuam em `active_subscribers()` por dedução própria (não estão listados no brief nem como "trocar" nem como "manter" explicitamente) — se a intenção fosse diferente, isso precisaria ser revisitado numa task futura (possivelmente Task 8, que é citada no prompt como responsável por `platform_usage_service.py`).
3. Nenhuma migration, nenhuma mudança em `charges.py` ou `platform_usage_service.py` — conforme restrição.

## Status

DONE.
