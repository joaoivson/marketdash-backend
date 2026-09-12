# Cobrir as publicações da automação — 12/09/2026

Continuação de `STATUS-instagram-automacao-parada.md`, que fechou com
"a automação cobre 9 de 278 posts — decisão de produto".

| # | Etapa | O que foi feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Medir palavra × genérico | 66,1% comentam a palavra do post; 27,2% "quero" | eu | — | ✅ | — |
| 2 | Descartar "automação por palavra" | não reduz a contagem: 1 link por produto | eu | ✅ | — | — |
| 3 | Precedência entre automações | `ORDER BY id` + especificidade no pipeline | eu | ✅ | — | — |
| 4 | Palavra a partir da legenda | 260/283 (92%), **nenhuma errada** | eu | ✅ | ✅ | — |
| 5 | `/media` devolve cobertura e sugestão | `tem_automacao`, `palavra_sugerida` | eu | ✅ | ✅ | — |
| 6 | `POST /automations/lote` | modelo × itens, pula coberto, reporta pulados | eu | ✅ | ✅ | — |
| 7 | Tela `/dashboard/automacoes/em-lote` | cabeçalho-diagnóstico, modelo, palavra+link | eu | ✅ | ✅ | ✅ |
| 8 | Validação visual 390px e 1440px | Playwright contra hml | eu | ✅ | ✅ | ✅ |
| 9 | Deploy | push develop (hml) e cherry-pick p/ produção | **João + eu** | ⬜ | ⬜ | ⬜ |

## O bug que a rodada destampou

`active_automations_for_connection` fazia `.all()` **sem `ORDER BY`** e o
pipeline responde com a PRIMEIRA que casa. Uma automação "qualquer post" com a
palavra "quero" roubaria os comentários das específicas — **não
deterministicamente**, porque a ordem vinha do Postgres. Sintoma: "às vezes vem
o link errado", que não reproduz.

Não era feature nova: era bug esperando o gatilho, e o gatilho é exatamente o
caminho que a aluna tomaria.

## O que a validação na tela derrubou

A barra de ação `sticky bottom-*` da primeira versão:

1. **não engatava** — ficava no fim de uma página de ~28 mil px (118
   publicações). `boundingClientRect().bottom = 27894` contra viewport de 844.
   O botão "Criar" não existia para quem não rolasse tudo;
2. se engatasse como `fixed bottom-0`, **cobriria o MobileBottomNav**.

`tsc` verde, lint verde e 1002 testes verdes não disseram nada sobre nenhum dos
dois. Virou ação em fluxo normal (topo e fim) — revalidado: botão na primeira
dobra em 390px (y=137) e 1440px (y=113), sem overflow horizontal, sem erro de
console, sem 4xx na API.

## Commits

| repo | sha | o quê |
|---|---|---|
| backend | `564c162` | precedência, palavra da legenda, lote, `/media` enriquecido |
| frontend | `358361b` | tela `/dashboard/automacoes/em-lote` |

**Sem migration** — a rodada não toca no schema.

## O que continua irredutível

O número de automações é o número de PRODUTOS: cada post vende um produto com
link próprio. Nenhum escopo resolve isso — `ESCOPO_QUALQUER` tem um único link.
O que a rodada mudou foi o CUSTO de criar cada uma (palavra pré-preenchida,
modelo compartilhado, criação em bloco de até 50).
