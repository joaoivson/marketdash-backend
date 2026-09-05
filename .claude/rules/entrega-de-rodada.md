# Como uma rodada termina

Vale para rodada de documento (`*_alteracoes.md`) e para lista de ajustes solta
no chat. Os três passos são obrigatórios e nesta ordem.

## 1. Tabela item a item — não um resumo

A entrega fecha com uma **tabela cobrindo todos os itens pedidos**, cada um em
um de três estados:

| Estado | Quando |
|---|---|
| ✅ **OK** | implementado, verificado e no ar |
| ⚠️ **Pendente** | feito, mas falta deploy, migration em produção, validação com dado real, ou ação de alguém |
| ❌ **Não implementado** | ficou de fora — **com o motivo** |

**Por que tabela e não prosa.** Um resumo bem escrito soa completo mesmo quando
não está. Em 05/09/2026 um item 🟢 do documento (o aviso ao tirar o último grupo
da rotação) passou despercebido em **dois resumos seguidos** — só apareceu ao
conferir linha a linha contra o documento.

Regras da tabela:

- **Verifique no código, não de memória.** Auditar de memória é exatamente como
  o item se perdeu. Em rodada grande, uma passada de auditoria por item.
- Item que o documento já marcou como corrigido **entra na tabela também** —
  confirmar é informação.
- Item que o documento pediu e a **medição mostrou não ser bug** entra como ✅
  com a nota do que foi medido. Não sumir da tabela.
- Pendência diz **de quem é a bola**: minha, do João, ou de terceiro.
- "Quase" é ⚠️, nunca ✅.

## 2. Documentação

`CHANGELOG.md` (o fato, visível ao usuário) + `.claude/memoria/` (o porquê:
`DIARIO.md` append-only, `DECISOES.md`, `CONTEXTO.md`). Migration nova entra no
runbook `docs/PROMOCAO_PARA_PRODUCAO.md`, no inventário **e** na ordem de
execução.

## 3. Validação na tela

Screenshot em **todos** os pontos afetados, nos dois tamanhos. `tsc` verde,
lint verde e pytest verde não dizem nada sobre o que ela vê.

E **CI verde não é deploy feito** — confirme por um marcador do código novo
servido pela URL real.
