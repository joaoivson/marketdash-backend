# Task 5 Report: Orquestração do Diagnóstico

## Status
**DONE**

## Commit
`99f6cc3` — feat(ia): orquestração do diagnóstico com estado terminal garantido

## Resumo

Implementada a task que amarra o Diagnóstico IA de ponta a ponta:
`AiDiagnosticService.gerar()` valida saldo, monta o snapshot, chama a IA,
persiste e só então debita crédito; `AiDiagnosticService.responder()` faz o
mesmo para o chat, com teto de mensagens e isolamento por usuária.

Os dois invariantes do brief foram verificados nos testes:
1. **A sessão nunca fica em `gerando`** — `test_sessao_nunca_termina_em_gerando`
   força três motivos de erro (`timeout`, `http`, `formato`) e confirma que o
   status final nunca é `"gerando"`.
2. **Falha da IA não debita crédito** — `test_falha_da_ia_marca_erro_e_nao_debita`
   e `test_falha_no_chat_nao_debita` confirmam `credito.debitos == []` quando
   `ErroIA` é levantada.

## Arquivos Criados

1. **`tests/unit/test_ai_diagnostic_service.py`** — 9 testes, copiados
   verbatim do brief (Step 1), usando fakes de repository/cliente/snapshot/
   crédito — nenhuma escrita em banco, nenhuma chamada real à OpenAI.
2. **`app/repositories/ai_diagnostic_repository.py`** — `AiDiagnosticRepository`
   com `criar/salvar/buscar/em_andamento/listar/adicionar_mensagem/
   listar_mensagens/contar_mensagens_da_usuaria`. Copiado verbatim do brief
   (Step 3); consome `AiDiagnostic`/`AiDiagnosticMessage`/`STATUS_GERANDO` já
   existentes em `app/models/ai_diagnostic.py`.
3. **`app/services/ai_prompts.py`** — `SISTEMA_RELATORIO`, `SISTEMA_CHAT`,
   `montar_entrada_relatorio`, `montar_contexto_chat`. Base do brief (Step 4)
   **com um ajuste deliberado**: ver seção "Decisão aplicada ao prompt".
4. **`app/services/ai_diagnostic_service.py`** — `AiDiagnosticService`,
   `PeriodoVazio`, `GeracaoEmAndamento`, `LimiteDeMensagens`,
   `TETO_MENSAGENS = 20`, `MENSAGEM_POR_MOTIVO`. Copiado verbatim do brief
   (Step 5).

Nenhum arquivo consumido (`ai_diagnostic.py`, `ai_credit_service.py`,
`ai_snapshot_service.py`, `openai_client.py`) foi alterado.

## Decisão aplicada ao prompt (gasto vs investimento_ads)

O snapshot (`AiSnapshotService.montar`) expõe duas chaves de gasto com
significados diferentes:
- `kpis["gasto"]`: vem do rateio de `DatasetRow.cost` sobre as linhas de
  venda — só existe (é diferente de zero) quando houve venda no período, e é
  o valor já usado nos cálculos de lucro/ROAS que chegam prontos.
- `kpis["investimento_ads"]`: soma bruta direta da tabela `ad_spends` no
  período — existe mesmo que não tenha havido nenhuma venda (o cenário mais
  crítico: dinheiro queimado, retorno zero).

Sem instrução explícita, a IA tende a somar as duas chaves (dobrando o gasto
narrado) ou a citar `"gasto" = 0` num período com investimento real,
escondendo o prejuízo exatamente no caso mais grave. Adicionei a regra 6 em
`SISTEMA_RELATORIO`:

```
6. Em "kpis" existem DUAS chaves de gasto com anúncio, e elas NÃO são a mesma
coisa nem podem ser somadas: "gasto" é o investimento em anúncio já rateado
sobre as vendas do período (só existe quando houve venda, e é o valor usado
nos cálculos de lucro e ROAS que vêm prontos); "investimento_ads" é a soma
bruta de tudo que foi gasto em anúncio no período, direto da fonte, e existe
mesmo sem nenhuma venda. Quando quiser descrever "quanto foi investido em
anúncio" no geral, use "investimento_ads". Se houver "investimento_ads"
maior que zero e "gasto" igual a zero, isso significa dinheiro gasto em
anúncio sem nenhuma venda no período — narre isso como prejuízo, nunca como
ausência de dado.
```

E a regra 5 equivalente em `SISTEMA_CHAT`, para o chat (que reusa o mesmo
snapshot congelado) não reintroduzir a confusão em uma pergunta de
acompanhamento:

```
5. Se a pergunta envolver gasto com anúncio, lembre que "gasto" (rateado sobre
vendas) e "investimento_ads" (soma bruta da fonte) são números diferentes —
nunca some os dois nem confunda um pelo outro.
```

Todo o resto do texto dos prompts (`SISTEMA_RELATORIO`, `SISTEMA_CHAT`,
`montar_entrada_relatorio`, `montar_contexto_chat`) é idêntico ao brief.

## Comandos e Saídas Literais

### Step 2 — teste falhando antes da implementação

```
$ python -m pytest tests/unit/test_ai_diagnostic_service.py -q
ImportError while importing test module '.../tests/unit/test_ai_diagnostic_service.py'.
E   ModuleNotFoundError: No module named 'app.services.ai_diagnostic_service'
=========================== short test summary info ============================
ERROR tests/unit/test_ai_diagnostic_service.py
1 error in 0.33s
```

### Step 6 — teste passando após implementação

```
$ python -m pytest tests/unit/test_ai_diagnostic_service.py -v
tests/unit/test_ai_diagnostic_service.py::test_geracao_com_sucesso_fica_pronta_e_debita_10 PASSED
tests/unit/test_ai_diagnostic_service.py::test_falha_da_ia_marca_erro_e_nao_debita PASSED
tests/unit/test_ai_diagnostic_service.py::test_sessao_nunca_termina_em_gerando PASSED
tests/unit/test_ai_diagnostic_service.py::test_periodo_vazio_nem_chama_a_ia PASSED
tests/unit/test_ai_diagnostic_service.py::test_sem_saldo_nem_chama_a_ia PASSED
tests/unit/test_ai_diagnostic_service.py::test_chat_debita_1_credito_e_grava_as_duas_pontas PASSED
tests/unit/test_ai_diagnostic_service.py::test_chat_respeita_o_teto_de_mensagens PASSED
tests/unit/test_ai_diagnostic_service.py::test_chat_de_sessao_de_outra_usuaria_nao_responde PASSED
tests/unit/test_ai_diagnostic_service.py::test_falha_no_chat_nao_debita PASSED

============================== 9 passed in 0.24s ===============================
```

### Suite completa (comando exigido pela task)

```
$ python -m pytest tests/unit -q
...
FAILED tests/unit/test_shopee_upsert_additive.py::test_sync_commissions_never_deletes_the_window
FAILED tests/unit/test_shopee_upsert_additive.py::test_sync_commissions_flags_suspected_partial_without_blocking_write
FAILED tests/unit/test_shopee_upsert_additive.py::test_guard_ignores_first_day_of_window
3 failed, 277 passed, 17 warnings in 1.49s
```

**Linha final literal: `3 failed, 277 passed, 17 warnings in 1.49s`**

Baseline era 268 passando + 3 falhas pré-existentes. 277 = 268 + 9 (os testes
novos desta task). As 3 falhas são exatamente as mesmas de
`test_shopee_upsert_additive.py` da baseline — não foram tocadas, nenhuma
falha nova foi introduzida.

## Auto-Revisão

✅ Passos seguidos na ordem exata do brief (teste → falha → repository →
prompts → serviço → passa → commit).
✅ Teste rodou e falhou com `ModuleNotFoundError` antes da implementação.
✅ Repository e serviço implementados verbatim conforme o brief — nenhuma
lógica adicional, nenhum atalho.
✅ Prompt ajustado exatamente na dimensão pedida (gasto vs investimento_ads),
sem tocar no restante do texto nem na estrutura do JSON de saída.
✅ Nenhuma peça já pronta (`ai_diagnostic.py`, `ai_credit_service.py`,
`ai_snapshot_service.py`, `openai_client.py`) foi alterada.
✅ Nenhuma chamada real à OpenAI (só `_FakeCliente` nos testes).
✅ Nenhuma escrita em banco (só `_FakeRepo`, um `SimpleNamespace` em memória).
✅ Invariante 1 (nunca `gerando`) coberto por teste dedicado e verificado
também nos caminhos de `PeriodoVazio`/`SaldoInsuficiente` (nesses casos a
sessão nem chega a ser criada, então não há "gerando" pendente).
✅ Invariante 2 (falha não debita) coberto tanto em `gerar` quanto em
`responder`.
✅ Código e comentários em português; comentários explicam o porquê (ex.:
"Chamada ANTES de gravar: falha não deixa pergunta órfã nem debita").
✅ Branch `develop`, commit único, sem merge/rebase/push para `main`.
✅ Baseline de testes mantida: 268 → 277 passando (soma exata dos 9 novos),
3 falhas pré-existentes intocadas.

## Preocupações

1. **Corrida entre checagem de saldo e débito** (herdada do brief, não
   introduzida por mim): `tem_saldo()` e `debitar()` são duas chamadas
   separadas. Se duas requisições da mesma usuária correrem em paralelo, a
   checagem de saldo em `gerar()`/`responder()` pode passar para ambas antes
   de qualquer débito — mas `AiCreditService.debitar()` já é atômico via
   advisory lock no repository, então o pior cenário é: a análise é gerada
   (ou a resposta do chat é gravada) e o débito subsequente falha com
   `SaldoInsuficiente` por já não haver mais saldo — nesse caso a sessão fica
   `pronto`/a mensagem fica gravada, mas sem cobrar. Não fere os dois
   invariantes centrais (nunca fica "gerando"; falha da IA não debita), mas
   é uma janela de "análise grátis" em corrida de cliques duplos que a Task 7
   ou uma revisão futura pode querer fechar (ex.: verificar saldo de novo, ou
   reservar crédito antes da chamada). Não alterei nada aqui porque o brief
   pede código verbatim e o teste não cobre esse cenário.
2. **`GeracaoEmAndamento`** é produzida pelo serviço mas não tem teste
   dedicado nesta task (o brief não pede um) — fica não verificada por teste
   automatizado até uma task futura exercitá-la (provavelmente a rota da
   API).

---

## Fix pós-revisão de código (2026-08-06)

Revisão de código encontrou três defeitos nesta mesma orquestração — os dois
primeiros exatamente na "Preocupação 1" acima, que na época foi registrada
mas não corrigida por o brief pedir código verbatim.

### Defeito 1 (Crítico) — sessão podia ficar presa em "gerando" para sempre

Em `gerar()`, só `ErroIA` era capturada depois de `self.repo.criar(...)`
commitar a sessão como `gerando`. Qualquer outra exceção (erro de banco no
commit, conexão caída, bug no cliente) subia crua e deixava a linha presa em
`gerando` permanentemente — a próxima tentativa da usuária bateria em
`em_andamento()` e receberia `GeracaoEmAndamento` para sempre.

Fix: `gerar()` agora tem um `except Exception` genérico ao redor da chamada à
IA, e também ao redor do `self.repo.salvar(sessao)` final — ambos delegam
para um novo helper `_marcar_erro(sessao, mensagem)`. Esse helper, por sua
vez, tem seu próprio `try/except` ao redor do `repo.salvar`: se até a
gravação do estado de erro falhar, a exceção é capturada, logada com
`logger.exception`, e a função retorna sem relançar — relançar não resolveria
nada (só trocaria "presa por bug" por "presa por banco fora do ar", que já
apareceria no log de qualquer forma).

### Defeito 2 (Importante) — análise podia chegar sem ser cobrada

A ordem era: grava `pronto` → debita. Se o débito falhasse (corrida real:
`tem_saldo()` é leitura solta, duas requisições simultâneas passam nela antes
de qualquer débito), a sessão já estava `pronto` e visível, mas nunca fora
cobrada.

Fix: invertida a ordem em `gerar()` — com a resposta da IA em mãos, debita
primeiro (dentro de um `try/except`) e só então marca `relatorio`/`pronto` e
salva. Se o débito falhar, `_marcar_erro()` é chamado e `sessao.relatorio`
nunca é atribuído — a usuária não recebe uma análise que não pagou. Mesma
inversão em `responder()`: débito acontece depois da resposta da IA e antes
de gravar as mensagens do chat.

### Defeito 3 (Importante) — pergunta órfã no chat

`adicionar_mensagem` commitava a cada chamada; se a gravação da pergunta
tivesse sucesso e a da resposta falhasse, a pergunta ficava órfã no
histórico. Fix: novo método `AiDiagnosticRepository.adicionar_mensagens`
(plural) que recebe uma lista de `(papel, conteudo)` e faz **um único
commit** para todas — `responder()` agora grava pergunta+resposta juntas
nessa chamada. O comentário em `responder()` foi reescrito para descrever a
garantia real (chamada à IA antes de qualquer gravação; débito antes de
gravar; gravação atômica das duas mensagens), já que o comentário antigo só
cobria a falha da IA.

### Limpeza (Menor)

`contar_mensagens_da_usuaria` trocou a string literal `"user"` pela constante
`PAPEL_USUARIA`, importada de `app.models.ai_diagnostic`.

### Testes acrescentados

Em `tests/unit/test_ai_diagnostic_service.py`, três fakes novos
(`_FakeRepoFalhaAoSalvarPronto`, `_FakeCreditoQueFalhaAoDebitar`,
`_FakeRepoFalhaAoGravarMensagens`) e três testes:

1. `test_excecao_nao_erroia_durante_geracao_termina_em_erro_nunca_gerando` —
   repositório falha com `RuntimeError` (não `ErroIA`) ao salvar o estado
   `pronto`; confirma que a sessão termina em `erro`, nunca em `gerando`.
2. `test_debito_falha_apos_resposta_da_ia_deixa_sessao_em_erro_sem_entregar_analise`
   — crédito falha ao debitar mesmo com `tem_saldo()` tendo passado; confirma
   `status == erro` e `relatorio is None` (análise não entregue).
3. `test_falha_ao_gravar_resposta_do_chat_nao_deixa_pergunta_orfa` —
   `adicionar_mensagens` falha; confirma que `listar_mensagens` continua
   vazia (nem a pergunta sozinha sobra).

`_FakeRepo` ganhou o método `adicionar_mensagens` (espelhando o commit único
do repository real — só anexa à lista se todas as mensagens puderem ser
criadas).

### Comando e saída literal

```
$ python -m pytest tests/unit -q
...
FAILED tests/unit/test_shopee_upsert_additive.py::test_sync_commissions_never_deletes_the_window
FAILED tests/unit/test_shopee_upsert_additive.py::test_sync_commissions_flags_suspected_partial_without_blocking_write
FAILED tests/unit/test_shopee_upsert_additive.py::test_guard_ignores_first_day_of_window
3 failed, 280 passed, 17 warnings in 1.72s
```

**Linha final literal: `3 failed, 280 passed, 17 warnings in 1.72s`**

Baseline era 277 passando + 3 falhas pré-existentes. 280 = 277 + 3 (os testes
novos deste fix). As 3 falhas são as mesmas de `test_shopee_upsert_additive.py`
da baseline — não tocadas, nenhuma falha nova introduzida.

### Restrições respeitadas

- Custos (geração 10, chat 1), teto de 20 mensagens e "chat lê só o snapshot
  congelado" — inalterados.
- Prompt de sistema (`ai_prompts.py`) — não tocado.
- Nenhuma chamada real à OpenAI, nenhuma escrita em banco real (só fakes em
  memória, como já era o padrão do arquivo de teste).
- Código e comentários em português; comentários imprecisos após a mudança
  (docstring do módulo, docstring do arquivo de teste, comentário em
  `responder()`) foram corrigidos para descrever o comportamento atual.

### Caveat identificado (não coberto pelos testes pedidos)

Em `responder()`, a inversão "debita antes de gravar" fecha o buraco do
Defeito 2, mas abre uma janela simétrica menor: se o débito for bem-sucedido
e a gravação atômica das mensagens falhar logo em seguida, a usuária é
cobrada sem a resposta ficar visível no histórico (equivalente ao caso já
registrado na Preocupação 1 do relatório original para `gerar()`, quando o
`repo.salvar` final falha depois do débito). Não há transação compartilhada
entre `credito_svc` e `repo` para fechar isso por completo, e nenhum dos três
testes pedidos exercita esse caminho — registrado aqui para uma revisão
futura decidir se vale reservar/estornar crédito nesse cenário.

### Status
**DONE**

### Commit
`6f0f8c7` — fix(ia): garantir estado terminal, cobrança antes da entrega e sem pergunta órfã
