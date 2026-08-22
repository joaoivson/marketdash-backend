# Campanha "Ativa" pelo Status Real de Veiculação (Design)

**Origem:** doc "Regra — Campanha 'ativa' pelo status de veiculação real",
14/08/2026. Dois bugs: campanha **arquivada** (Daniele) aparecendo como
ativa; campanha de **orçamento vitalício esgotado** ("zumbi", Lilian) —
este segundo já tem fix parcial em produção (commits `3d60524`/`54768c0`/
`e3f3d8c`, heurística de 7 dias + `status_active_since`).

**Repos afetados:** `marketdash-backend` e `marketdash-frontend`.

## Correção de premissa (achado da investigação, diverge do doc original)

O doc propõe usar um "status de veiculação real" da Meta como fonte
distinta do `effective_status`, hipotetizando que a API talvez não
entregue esse campo hoje. **Não existe esse campo separado.** A coluna
"Veiculação da campanha" do export manual do Gerenciador de Anúncios é a
representação em UI do mesmo `effective_status` que o código já busca
(`app/services/facebook_marketing_client.py:245-252`,
`fields=...,effective_status`).

A causa raiz do bug de arquivamento é outra: `GET /{ad_account_id}/campaigns`
**sem parâmetro de filtro `effective_status` omite campanhas `ARCHIVED` e
`DELETED` da resposta por padrão** (documentação oficial da Meta:
*"A request with no filters returns only campaigns that were not
archived or deleted"*). Quando uma campanha é arquivada, ela some da
resposta da API; o sync (`facebook_integration_service.sync_user()`)
nunca mais a toca; o último `effective_status` conhecido (tipicamente
`ACTIVE`) fica congelado no banco pra sempre.

**Consequência pro resto do doc:** como não existe campo separado, a
heurística de 7 dias **não fica redundante** — ela cobre o caso zumbi
(orçamento esgotado, a Meta trava `effective_status=ACTIVE` propositalmente
e permanentemente), que o fix de arquivamento não toca. São duas causas
raiz diferentes, dois fixes diferentes, ambos necessários.

**Achado adicional, fora dos 2 bugs relatados:** os "3 lugares" que o doc
pede pra unificar (contagem, filtro, toggle) já divergem entre si hoje,
independente do bug de arquivamento — a contagem já aplica a heurística
de 7 dias + exclusão de anúncio reprovado
(`campaign_service.list_campaigns()`, linhas 274-281); o filtro de status
da lista e o campo `is_active` de cada campanha (que o toggle consome)
usam só `_is_active()` puro (`effective_status == "ACTIVE"`), sem a
heurística nem a exclusão.

## Abordagem — 4 partes

### Parte 1 — Sync passa a enxergar campanhas arquivadas

`facebook_marketing_client.list_campaigns()` (linhas 245-252) hoje chama
`GET /{ad_account_id}/campaigns` sem filtro. Adicionar o parâmetro
`filtering` da Graph API listando explicitamente todos os
`effective_status` que devem voltar — os que já voltam por padrão hoje
(`ACTIVE`, `PAUSED`, `PENDING_REVIEW`, `DISAPPROVED`, `PREAPPROVED`,
`PENDING_BILLING_INFO`, `CAMPAIGN_PAUSED`, `ADSET_PAUSED`, `IN_PROCESS`,
`WITH_ISSUES`) **mais `ARCHIVED`**. `DELETED` fica de fora
deliberadamente — campanha deletada não é "arquivada", é removida de
verdade; não faz sentido reintroduzi-la no MarketDash.

Depois que o sync passa a ver `ARCHIVED`, `upsert_campaign()`
(`campaign_repository.py:110-145`) já grava o valor normalmente — nenhuma
mudança necessária ali. `_is_active()` (`campaign_service.py:127-129`) já
exclui `ARCHIVED` corretamente (só retorna `True` pra `"ACTIVE"`) — o bug
some assim que o dado chega certo.

**Risco a validar na implementação:** a sintaxe exata do parâmetro
`filtering` (JSON-encoded, `[{"field":"effective_status","operator":"IN",
"value":[...]}]`) precisa ser testada contra a API real (ou um mock fiel
ao formato de resposta documentado) antes de confiar no fix — a
implementação deve incluir um teste que mocka a resposta HTTP e confirma
que o parâmetro é montado e enviado corretamente.

### Parte 2 — Uma função única de classificação, usada nos 3 lugares

Extrair uma função `_is_effectively_active(campaign, recent_activity_ids)`
em `campaign_service.py` que combina os 3 critérios hoje espalhados:

```python
def _is_effectively_active(campaign, recent_activity_ids) -> bool:
    return (
        _is_active(campaign)
        and not campaign.ad_review_issue
        and _still_delivering(campaign, recent_activity_ids)
    )
```

Usar essa função (não `_is_active()` puro) nos 3 pontos:
1. **Contagem** (`active_count_now`/`budget_now`) — já usa essa combinação
   hoje via list comprehension inline; passa a chamar a função extraída.
2. **Filtro de lista** (`status_filter == "active"`, linhas 288-291) — hoje
   só `_is_active(c)`; passa a usar `_is_effectively_active`.
3. **Campo `is_active` por campanha** (`_build_response`, consumido pelo
   toggle/badge do frontend) — hoje só `_is_active(campaign)`; passa a
   receber `recent_activity_ids` (já calculado uma vez por request de
   lista) e usar `_is_effectively_active`.

`recent_activity_ids` já é calculado uma única vez por chamada de
`list_campaigns()` — não introduz N+1, só precisa ser passado adiante pra
onde `is_active` é montado por campanha.

### Parte 3 — Toggle bloqueado para campanha arquivada

**Pedido literal do doc:** "o toggle fica bloqueado/desabilitado + tooltip
'campanha arquivada' no hover (não dá pra reativar)" — isso é só UI.

**Adição além do que o doc pede** (sinalizando explicitamente — decidir
se entra ou não): `PATCH /{campaign_id}/status`
(`app/api/v1/routes/campaigns.py`, linhas 152-170) também passaria a
rejeitar (400) tentativa de mudar status de campanha `ARCHIVED` no
backend, não só bloquear no frontend. Justificativa: bloqueio só de UI
não impede a chamada direta à API (nem garante que a Meta aceitaria a
reativação do jeito que o endpoint tenta hoje). Se preferir ficar
estritamente no que o doc pede (só UI), não implementar esta parte.

**Frontend** (`Campanhas.tsx`): o tipo `Campaign` já expõe
`effective_status` (`shared/types/campaign.ts:24-30`) — nenhuma mudança de
contrato de API necessária aqui. O `<Switch>` (linhas 697-703) passa a
receber `disabled={campaign.effective_status === "ARCHIVED"}`, com
tooltip "Campanha arquivada — não é possível reativar por aqui" no hover
(usar o padrão de tooltip já existente no design system do projeto, não
criar um novo componente).

### Parte 4 — Filtro de status expandido (literal ao doc: 4 categorias)

Hoje o filtro é binário (`all|active|paused`, `CampaignStatusFilter` em
`Campanhas.tsx`). O doc pede "todos os status reais (active / inactive /
archived / paused…)" — **4 termos distintos**, não 3. Correção em cima da
primeira versão deste design: "inactive" (o caso zumbi — Meta ainda marca
`effective_status=ACTIVE`, mas a campanha não entrega) é uma categoria
própria, **não** deve ser fundida com "paused" (campanha genuinamente
pausada, `effective_status` numa das variantes de pausa). São conceitos
diferentes pro usuário: uma ele pausou de propósito, a outra ele não sabe
que parou.

Mapeamento pros 4 filtros, usando o que o sistema consegue determinar
(sem campo de veiculação separado — deriva de `effective_status` +
heurística, igual às Partes 1-2):

- **Ativa** — passa `_is_effectively_active()` (mesmo critério do card e
  do toggle habilitado): `effective_status == "ACTIVE"` E não tem anúncio
  reprovado E passa a heurística de 7 dias.
- **Inativa** — `effective_status == "ACTIVE"` mas falha em
  `_is_effectively_active()` (zumbi de orçamento esgotado, ou anúncio
  reprovado) — tecnicamente "ativa" pra Meta, sem entrega real.
- **Pausada** — `effective_status`/`status` numa variante de pausa
  (`PAUSED`, `CAMPAIGN_PAUSED`, `ADSET_PAUSED`) — pausada de propósito,
  independente de entrega.
- **Arquivada** — `effective_status == "ARCHIVED"`.

Isso mantém "coerente entre toggle, filtro de status e contagem" (pedido
explícito do doc): "Ativa" no filtro é exatamente quem conta no card e
quem tem o toggle habilitado; "Arquivada" é exatamente quem tem o toggle
bloqueado.

Estados residuais da Meta não cobertos pelos 4 baldes acima (`PENDING_REVIEW`,
`DISAPPROVED`, `PREAPPROVED`, `PENDING_BILLING_INFO`, `IN_PROCESS`,
`WITH_ISSUES`) — o doc não menciona esses casos; ficam de fora do escopo
desta rodada, fora do filtro (não aparecem em nenhum dos 4 baldes; a task
de implementação decide um fallback razoável, ex. cair em "Inativa").

Backend: `status_filter` ganha os valores `"inactive"` e `"archived"`
(hoje só aceita `active`/`paused`/vazio) — reaproveitar o parâmetro
existente, não criar um novo.

## Global Constraints

- Não mexer no cálculo de markup/imposto/ROAS Real (fora de escopo,
  explícito no doc original).
- Não quebrar a heurística de 7 dias existente (commit `3d60524`) nem o
  fix de reativação (`status_active_since`, commit `54768c0`/`e3f3d8c`,
  migration `050`) — ambos continuam necessários, só passam a ser
  chamados a partir da função unificada em vez de inline na contagem.
- `DELETED` continua fora do escopo de sincronização — só `ARCHIVED` volta
  a ser buscado.
- Teste do parâmetro `filtering` da Graph API precisa mockar a chamada
  HTTP real (formato de request), não só a resposta — o risco aqui é
  montar o parâmetro errado e a Meta ignorá-lo silenciosamente (a API não
  erra em filtro malformado, só retorna vazio/tudo).

## Testes

- Unitário: `_is_effectively_active()` cobrindo os 4 casos combinados
  (ativa de verdade; zumbi; anúncio reprovado; arquivada) — estende
  `test_campaign_active_count_orcamento_esgotado.py` ou cria arquivo novo
  seguindo o mesmo padrão de fixtures.
- Unitário: `list_campaigns()` do `facebook_marketing_client.py` — mock da
  request HTTP, confirma que `filtering` é enviado com o formato e valores
  corretos (incluindo `ARCHIVED`, excluindo `DELETED`).
- Unitário: `PATCH /{campaign_id}/status` rejeita campanha arquivada (400).
- Integração/manual: sync real (ou fixture gravada) de uma campanha
  arquivada de verdade, confirmando que ela passa a ser gravada com
  `effective_status=ARCHIVED` e sai da contagem/filtro "ativa".

## Fora de escopo

- Cálculo de markup, imposto, ROAS Real.
- Reativar campanha arquivada via MarketDash (bloqueado, não implementado
  — a Meta não expõe isso de forma confiável via API pra esse caso).
- Status `DELETED` — não ressincronizado.
