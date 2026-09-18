# STATUS — Roteiros, rodada 2

Documento de origem: `marketdash_roteiros_alteracoes` (rodada 2), 18/09/2026.
Plano: `~/.claude/plans/marketdash-roteiros-swift-shore.md`.
Branches: `develop` nos dois repos.

Legenda — **Código**: ⬜ não começou · 🔄 em andamento · ✅ escrito com `pytest`/`tsc`/`lint` verdes.
**API**: ✅ só depois de conferir a resposta real do backend. **Tela**: ✅ só depois de validar no navegador via Playwright, comparando linha concreta (API × célula).

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| — | Migration 088 | `whatsapp_grupos.descricao` + tick do pg_cron 5min → 1min | eu | ✅ | ✅ aplicada em hml, job 105 ativo | — |
| H | Sem retry / trava de atraso | Expiração por passo que não começou; os 3 caminhos de parada expiram na hora; execução vira `falhou`; 2 avisos no Agendar | eu | ✅ | ⚠️ testes verdes; falta WAHA real | ✅ aviso do Agendar |
| A | Cancelar agendamento | Apaga pendentes, devolve a `rascunho`; roteiro encerrado só-leitura | eu | ✅ | ✅ roteiro 17: `cancelada`, 0 msgs, `rascunho` | ✅ ciclo completo |
| I | "Na fila" ≠ "Agendadas" | `contadores()` separa `pendente` por `agendado_para` | eu | ✅ | ✅ `agendadas:0 na_fila:0` no GET | ✅ linhas zeradas somem |
| F | Marcar todos | `mentions:["all"]` no 1º bloco com texto | eu | ✅ | ⚠️ falta número conectado | ✅ toggle reage ao texto |
| E | Blocos vídeo/áudio/arquivo | `sendVideo`/`sendVoice`(convert)/`sendFile`; upload 5/16/16/25 MB | eu | ✅ | ⚠️ falta número conectado | ✅ 5 botões na tela |
| C | Sync nome/descrição/imagem | Sync traz descrição; ações gravam na hora | eu | ✅ | ⚠️ falta ver o payload real do GOWS | — |
| M | Linha "Envios" | Bloco `envios` na visão geral + linha na tela | eu | ✅ | ✅ payload novo | ✅ "0 roteiros agendados · 0 mensagens…" |
| B | Vermelho só para falha | Vencido→âmbar, travado→apagado+cadeado, falha→vermelho | eu | ✅ | — | ✅ concluído sem vermelho nem aviso |
| J | Duplicar | Vai direto ao roteiro, sem o modal de datas | eu | ✅ | — | ✅ `/roteiros/17`, sem modal |
| K | Layout do editor | Barra fixa + faixa horizontal + blocos + prévia | eu | ✅ | — | ✅ desktop e 390px |
| G | Validação de data | `min` no campo, mínimo = minuto seguinte; prévia com data | eu | ✅ | — | ✅ trava o Concluir |
| L | Estado vazio + "Início" | Texto novo; rótulo "Início" no passo 1 | eu | ✅ | — | ✅ "Passo 1 de 1 · Início" |
| D | Coluna "Cheio" | Sim/Não resolvido + cadeado no override | eu | ✅ | ✅ `cheio` já vinha da API | ✅ "Não" em 901/1000 |
| N | Passo enviado travado | Era só verificar — **e estava quebrado** | eu | ✅ | — | ✅ cadeado, sem setas nem ✕ |
| — | Documentação | CHANGELOG + memória + runbook | eu | 🔄 | — | — |

## Decisões desta rodada (confirmadas com o João em 18/09)

1. **Tick de 1 min, tolerância de atraso de 3 min.**
2. **A trava de atraso vale para todo caminho de adiamento** — janela, teto
   diário e campanha pausada falham em vez de adiar, com o motivo real. Em
   troca, o Agendar ganha 2 avisos prévios.
3. **Limites de upload**: imagem 5 MB · áudio 16 MB · vídeo 16 MB · arquivo 25 MB.

## Destino da rodada (decidido pelo João em 18/09)

**Sobe só na `develop`** → deploy automático em homologação. **Sem merge para
`main`.** Produção fica PREPARADA, não promovida:

- migration 088 entra no inventário e na ordem do runbook
  (`docs/PROMOCAO_PARA_PRODUCAO.md`), com o aviso de que a **061 precisa rodar
  antes** dela em produção (`trigger_roteiros_tick` não existe lá);
- nada é aplicado no banco de produção nesta rodada.

## Pendências / bloqueios

**Uma só, e é de terceiro: não há número de WhatsApp conectado em hml.**
A tela de campanha avisa "Nenhum número conectado — os envios estão pausados".
Sem ele, quatro coisas ficam provadas só por teste unitário, nunca contra o
WAHA real:

| O que falta medir | Por que importa |
|---|---|
| `mentions: ["all"]` chega como menção no GOWS | é o bug 🔴 "Marcar todos não funciona"; o campo é **escondido do OpenAPI** do WAHA, então só o envio real confirma |
| Legenda de imagem aceita menção (o ❓ do documento) | se não aceitar, o toggle se restringe a bloco de texto |
| `sendVoice` com `convert:true` vira **bolha de áudio** | MP3/WebM sem conversão chegam como arquivo anexado — o oposto do que o bloco promete |
| Qual chave o GOWS usa para a descrição do grupo | leio `topic`/`grouptopic`/`description`/`desc`; foi essa diferença que fez 499 grupos virarem zero em silêncio |

**O que destrava:** parear um número na tela Configurações › WhatsApp › Números
em homologação e rodar um roteiro de teste num grupo próprio.

### Resíduo de teste em hml

Roteiro **17 "teste (cópia)"** na campanha 12 (conta de teste), em rascunho —
sobrou da validação do ciclo duplicar → agendar → cancelar. Inofensivo; não
apaguei porque não existe endpoint de exclusão e não quis rodar DELETE em
ambiente compartilhado sem necessidade.
