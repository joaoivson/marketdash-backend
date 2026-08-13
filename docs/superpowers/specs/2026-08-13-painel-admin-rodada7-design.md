# Painel Admin — Rodada 7 (Design)

**Origem:** brief de Luiz Fernando, 12/08/2026 ("Painel Admin — Correções Rodada 7").
**Repos afetados:** `marketdash-backend` e `marketdash-frontend`.
**Contexto:** Rodada 6 (dedupe por `order_ref`, MRR/ativos redefinidos, taxa de
renovação) já está concluída e deployada em produção. Esta rodada é de
acabamento: 10 itens, 3 deles reincidências de rodadas anteriores (marcadas
♻️ no brief original).

## Abordagem

Os 10 itens do brief se dividem em três grupos de tratamento, definidos após
mapear o código atual (não assumindo que a descrição do sintoma implica a
causa proposta no brief):

**Grupo A — fixes diretos**, onde a causa já foi localizada e confirmada
lendo o código atual.

**Grupo B — investigação orientada por dado real antes de decidir se há
fix**, para os itens onde o código já parece implementar exatamente o que o
brief pede — corrigir aqui sem evidência arriscaria alterar lógica que já
está correta.

**Grupo C — script de validação**, replicando o padrão de
`scripts/validar_rodada6.py`: roda os 11 testes de aceite do brief contra
produção, cedo no plano (não só ao final), servindo de evidência para o
Grupo B antes de qualquer fix ser escrito para ele.

## Grupo A — Fixes diretos

### Item 2 — Denominador do churn

`AdminMetricsService.churn_for_month()` (`app/services/admin_metrics_service.py:512`)
usa `active_subscribers(as_of=...)` (base de acesso, ~37) como denominador.
Trocar para `renewing_subscribers(as_of=...)` (~20 em 01/08) — assinantes
renovando no início do mês, consistente com a redefinição de "ativos" da
Rodada 6.

**Escopo do impacto:** `churn_for_month` alimenta `dashboard()["churn_rate"]`
e `["churn_count"]`, e é consumida por `new_vs_canceled_series()` — mudar o
denominador afeta só `rate`, não `count` (o gráfico de barras usa só
`count`, não muda).

**Aceite:** churn de agosto ≈ 20% (4 ÷ renovando em 01/08).

### Item 3 — Bruto do MRR usa preço de tabela, não última cobrança

Decisão de negócio confirmada com o usuário: "bruto" = preço de tabela
vigente do plano/periodicidade, não o valor real da última cobrança paga
(que pode vir com desconto/cupom histórico).

`AdminMetricsService.mrr_cents()` (`app/services/admin_metrics_service.py:390`)
hoje usa `paid.amount_gross_cents` (última cobrança paga) como `g` antes de
dividir por `_freq_divisor`. Trocar a fonte de `g` para
`list_price_cents(ev.plan, ev.plan_frequency)` — catálogo de preços já
existe em `app/core/plans.py:71-133` (ex.: `("pro", "trimestral"): 14700`).
Mantém a soma em ponto flutuante por assinante e arredondamento só no total
(já implementado corretamente, não mexer nisso).

`net` continua vindo da última cobrança paga (não faz parte do pedido do
brief; é o valor líquido real recebido).

**Aceite:** bruto = R$1.766,50 com a base de 12/08 (13 Pro M + 6 Ess M + 11
Pro T + 2 Pro A → 871 + 282 + 539 + 74,50).

### Item 5 — Janela de período (7d/30d/90d) alinhada a dia civil BRT

`PlatformUsageService._inicio()` (`app/services/platform_usage_service.py:103`)
calcula a janela como `agora - timedelta(days=dias)` em UTC — um instante,
não um conjunto de dias civis. Ao agrupar por `cast(logged_at, Date)` em
`usuarias_por_dia()`, uma janela de 7 dias cobre pedaços de até 8 datas
diferentes.

Já existe função única (`_inicio`) reaproveitada por todos os consumidores
(`_logins_do_periodo`, `usuarias_por_dia`, `uso_de_links_e_paginas`,
`telas_mais_acessadas`, e `admin_metrics_service._uso_links_paginas_30d`) —
não há múltiplas queries divergentes para unificar, só a fórmula de
`_inicio` precisa mudar. Corrigir para alinhar a dias civis em BRT,
reaproveitando o padrão já usado em `admin_metrics_service.py`
(`_brt_date`, `_month_bounds`): 7 dias = hoje (BRT) + 6 anteriores, do
início do dia mais antigo ao fim de hoje.

**Aceite:** filtro 7d → máximo em "Dias ativos" = 7; gráfico com 7 barras;
cards e tabela somando a mesma janela.

### Item 6 — Label do MRR cortado

`AdminDashboard.tsx` (linhas ~155-172): `LineChart`/`BarChart` sem prop
`margin`, o SVG do `ResponsiveContainer` clipa o label do último ponto por
ficar colado na borda direita da área de plot. Adicionar `margin={{ top:
20, right: 24, left: 0, bottom: 0 }}` (ajustar `right` conforme teste
visual) nos dois gráficos (MRR e Faturamento, que têm o mesmo padrão),
reaproveitando a referência de `EvolutionBarChart.tsx`.

### Item 7 — Grid 2×2 no Dashboard

`AdminDashboard.tsx`: remover `className="lg:col-span-2"` dos cards "Novas
× canceladas" e "Plano × periodicidade" — caem nas duas colunas do grid
existente (`grid gap-4 lg:grid-cols-2`), formando 2×2 com MRR + Faturamento
em cima.

### Item 8 — Paginação da lista de Clientes

Backend: `export_clients_csv` (`app/api/v1/routes/admin_panel.py:127`) hoje
chama `list_clients({})` hardcoded, ignorando qualquer filtro. Alterar para
aceitar os mesmos query params de `GET /admin/clients` (q, status, plano,
alertas) e repassar para `list_clients(filters)`.

Frontend: `AdminClientsPage` (`AdminClients.tsx`) busca toda a base
filtrada via `fetchAdminClients` (sem paginação no backend, client-side já
é o padrão correto aqui). Reaproveitar `Paginacao`/`AdminTableFooter`
(`src/features/admin/components/AdminTableFooter.tsx`, já usado em
`AdminSyncStatus.tsx`, `LINHAS_POR_PAGINA = 20`) — ajustar o texto do
rodapé para o formato "Mostrando 1–20 de 32" pedido pelo brief (hoje é
"{total} registros · página {pagina} de {paginas}"). Resetar página para 1
no `useEffect` que reage a `filters` e em `toggleSort`. `exportCsv` passa a
montar a querystring com os mesmos `filters` do `useMemo` usado por
`fetchAdminClients`.

**Aceite:** 20/página, "Mostrando X–Y de N"; busca varre a base inteira
(client-side sobre o array já filtrado); trocar ordenação/filtro volta à
página 1; CSV exporta tudo que o filtro pega.

### Item 9 — "—" para recurso fora do plano

`planLimit(plan, "links" | "paginas_captura")` e `isUnlimited()` já
existem em `src/shared/lib/plans.ts` (essencial tem limite 0 para ambos).
Faltam dois pontos:

1. Backend: a resposta de atividade por usuária (consumida por
   `PlatformUsageTab`, tipo `PlatformUsage["atividade"]` em
   `admin-panel.service.ts`) não inclui o plano do usuário — precisa
   adicionar esse campo.
2. Frontend: `PlatformUsageTab.tsx` (linhas ~232-237) e
   `AdminClientDetail.tsx` (linhas ~204-207, que já tem `plan` disponível
   via `sub.plan || data.plan`) passam a checar `planLimit(plan, recurso)
   === 0` e renderizar "—" em vez de "0/0".

Fora do escopo desta rodada (nota do brief, não um item numerado): investigar
se "criados" (Y) deveria ser constante e não variar por período — item
separado, não faz parte dos 10 pontos, só registrar como observação.

**Aceite:** Essencial mostra "—" em Links/Páginas; Pro mantém X/Y.

### Item 10 — Card "Sem acesso 10d+" → filtro real

Bug confirmado: `PlatformUsageTab.tsx:146` manda
`?sem_acesso=${dias}`, mas `AdminClients.tsx:144` só lê
`no_login_10d`. Trocar o link para `?no_login_10d=1`. O backend já usa a
mesma definição em ambos os lugares (`UserLogin` mais recente por usuário,
corte de 10 dias, "nunca logou" conta) — não precisa mudar backend.

Nuance a resolver na implementação: `cards().sem_acesso_10d`
(`platform_usage_service.py`) conta só sobre `_base_ativa()` (exclui
admin/demo, restringe a quem tem acesso vigente); `list_clients(no_login_10d=True)`
sozinho não tem essa restrição de status. Conferir se o filtro de status
padrão de `AdminClientsPage` já cobre isso; se não, o link precisa
combinar `no_login_10d=1` com o filtro de status equivalente para o número
do card bater exatamente com o tamanho da lista (requisito explícito do
brief: "número do card = número de linhas da lista, sempre").

## Grupo B — Investigação antes de decidir o fix

### Item 1 — Novas de julho (6 vs. 8 esperado)

`new_subscriptions()` (`app/services/admin_metrics_service.py:478`) já
conta pela primeira cobrança paga histórica do assinante (`first_paid`,
sobre `PAID_EVENTS`), sem nenhum filtro por status atual — isso já é o
comportamento pedido pelo brief ("cancelamento posterior não reescreve o
mês de entrada"). Hipótese a testar: linhas com `is_plan_change` NULL (em
vez de `False`) somem silenciosamente do filtro `.is_(False)` da query
(linha 486).

**Ação:** `scripts/validar_rodada7.py` roda `new_subscriptions(2026, 7)`
contra produção e compara com 8. Se bater 8, o item já está resolvido —
documentar e não tocar no código. Se bater 6, investigar a causa raiz real
(incluindo checar se `is_plan_change` NULL existe nos dados de julho) e só
então escrever o fix.

### Item 4 — Registro de acesso (1h contínua deveria gerar 1 registro)

Fluxo principal já parece protegido: `AccessBeacon.tsx` só dispara
`POST /access` em `SIGNED_IN` (ignora `TOKEN_REFRESHED` e
`INITIAL_SESSION`), e `daily_access_service.record_access()` dedupe numa
janela de 2 minutos. Existe um caminho paralelo sem essa proteção:
`POST /api/v1/auth/login` (`app/api/v1/routes/auth.py:29-56`, fallback
legado pré-Supabase) grava `UserLogin` direto, sem dedupe.

**Ação (conforme pedido explícito do brief — "executar e reportar"):**
1. Rodar o teste da 1 hora: usar o app normalmente por 1h sem deslogar,
   confirmar se gera 1 registro.
2. Medir, para o período de alta contagem de Daniele (126 acessos em 8
   dias), quantos vieram do caminho legado (`auth.py`) vs. do principal
   (Supabase + `AccessBeacon`).
3. Se o teste da 1h passar E a maioria dos acessos de Daniele vier do
   caminho legado → aplicar a mesma janela de dedupe de 2min ao insert de
   `auth.py:49`, ou migrar esse fallback para usar `record_access()`.
4. Se o teste da 1h falhar no fluxo principal → investigar
   `AccessBeacon`/`record_access` diretamente.

## Grupo C — Script de validação

`scripts/validar_rodada7.py`, mesmo padrão de `scripts/validar_rodada6.py`
(conexão à produção via `.env.backup-1208`/DSN com host trocado, mesma
convenção da Rodada 6). Cobre os 11 testes de aceite do brief:

1. Novas×Canceladas: julho 8×6; agosto mantém 16×4.
2. Churn de agosto com denominador = renovando em 01/08 (≈20%).
3. Bruto do MRR = R$1.766,50 com a base de 12/08.
4. Teste da 1h executado e reportado.
5. Filtro 7d: Dias ativos ≤ 7, gráfico com 7 barras, cards e tabela na
   mesma janela.
6. Label do último ponto do MRR visível por inteiro (checagem visual).
7. Dashboard em grid 2×2 (checagem visual).
8. Clientes paginado; busca varre tudo; CSV completo.
9. Essencial mostra "—"; Pro mantém X/Y; Y constante (observação separada,
   não bloqueia o aceite deste item).
10. Card "Sem acesso 10d+" = lista aberta com o mesmo N, chip de filtro
    visível e removível.
11. Nada quebrado: sync Shopee/Meta, OAuth, pausar/ativar, orçamento
    (regressão manual).

Rodar **cedo** no plano (não só ao final, diferente da Rodada 6) — os
resultados dos itens 1 e 3 do checklist determinam se o Grupo B precisa de
fix ou só de documentação.

## Fora de escopo desta rodada

- Item 9, observação sobre "criados" (Y) variar por período — não é um dos
  10 itens numerados, só uma nota do brief a registrar.
- Chip de filtro removível (item 10) na UI de Clientes — se `AdminClients`
  já tem um padrão de chip de filtro ativo reaproveitável, usar; senão,
  avaliar no plano de implementação se cabe nesta rodada ou é um item à
  parte (o brief pede explicitamente "chip removível com ×", então faz
  parte do aceite do item 10 — mantido no escopo, só sinalizando que pode
  exigir um componente novo).

## Testes

Cada item do Grupo A ganha teste unitário/de integração cobrindo a
correção (seguindo o padrão de `tests/unit/test_*` já usado nas rodadas
anteriores). Grupo B não ganha teste de código até a investigação
confirmar se há fix — o próprio script de validação é a evidência inicial.
