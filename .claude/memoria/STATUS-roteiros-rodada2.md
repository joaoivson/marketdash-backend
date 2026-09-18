# STATUS — Roteiros, rodada 2

Documento de origem: `marketdash_roteiros_alteracoes` (rodada 2), 18/09/2026.
Plano: `~/.claude/plans/marketdash-roteiros-swift-shore.md`.
Branches: `develop` nos dois repos.

Legenda — **Código**: ⬜ não começou · 🔄 em andamento · ✅ escrito com `pytest`/`tsc`/`lint` verdes.
**API**: ✅ só depois de conferir a resposta real do backend. **Tela**: ✅ só depois de validar no navegador via Playwright, comparando linha concreta (API × célula).

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| — | Migration 088 | `whatsapp_grupos.descricao` + tick do pg_cron de 5min → 1min | eu | ✅ | — | — |
| H | Sem retry / trava de atraso | Expiração por passo que não começou; os 3 caminhos de parada expiram na hora; execução vira `falhou`; 2 avisos novos no Agendar | eu | ✅ | ⬜ | ⬜ |
| A | Cancelar agendamento | Apaga pendentes, devolve roteiro a `rascunho`; roteiro encerrado vira só-leitura (409 em salvar/datas/agendar) | eu | ✅ | ⬜ | ⬜ |
| I | "Na fila" ≠ "Agendadas" | `contadores()` separa `pendente` por `agendado_para`; schemas ganham os 2 campos | eu | ✅ | ⬜ | ⬜ |
| F | Marcar todos | `mentions:["all"]` no primeiro bloco com texto; `waha_client` ganha o parâmetro | eu | ✅ | ⬜ | ⬜ |
| E | Blocos vídeo/áudio/arquivo | `sendVideo`/`sendVoice`(convert)/`sendFile` no client; upload por tipo com limite 5/16/16/25 MB | eu | ✅ | ⬜ | ⬜ |
| C | Sync nome/descrição/imagem | Sync passa a trazer descrição; ações gravam no registro local na hora | eu | ✅ | ⬜ | ⬜ |
| M | Linha "Envios" | Bloco `envios` no payload de visão geral + linha na tela | eu | 🔄 | ⬜ | ⬜ |
| B | Vermelho só para falha | Vencido→âmbar, travado→apagado+cadeado, falha→vermelho; roteiro encerrado sai do jogo | eu | ⬜ | — | ⬜ |
| J | Duplicar | Leva direto ao roteiro, sem abrir o modal de datas | eu | ⬜ | — | ⬜ |
| K | Layout do editor de passo | Barra fixa (Voltar·nome·Passo X de Y·Concluir) + faixa horizontal + blocos + prévia | eu | ⬜ | — | ⬜ |
| G | Validação de data | `min` no campo, mínimo = minuto seguinte, horário resolvido; prévia mostra a data | eu | ⬜ | — | ⬜ |
| L | Estado vazio + "Início" | Texto novo do estado vazio; rótulo "Início" no passo 1 | eu | ⬜ | — | ⬜ |
| D | Coluna "Cheio" | Mostra Sim/Não resolvido + cadeado quando há override | eu | ⬜ | — | ⬜ |
| N | Passo enviado travado | Só verificar — o código já esconde setas e ✕ (`RoteiroEditor.tsx:592`) | eu | — | — | ⬜ |
| — | Documentação | CHANGELOG + DIARIO/DECISOES/CONTEXTO + runbook de promoção | eu | ⬜ | — | — |

## Decisões desta rodada (confirmadas com o João em 18/09)

1. **Tick de 1 min, tolerância de atraso de 3 min.**
2. **A trava de atraso vale para todo caminho de adiamento** — janela, teto
   diário e campanha pausada falham em vez de adiar, com o motivo real. Em
   troca, o Agendar ganha 2 avisos prévios.
3. **Limites de upload**: imagem 5 MB · áudio 16 MB · vídeo 16 MB · arquivo 25 MB.

## Pendências / bloqueios

Nenhum até aqui.
