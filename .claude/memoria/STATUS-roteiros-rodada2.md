# STATUS — Roteiros, rodada 2

Documento de origem: `marketdash_roteiros_alteracoes` (rodada 2), 18/09/2026.
Plano: `~/.claude/plans/marketdash-roteiros-swift-shore.md`.
Branches: `develop` nos dois repos.

**NO AR EM HOMOLOGAÇÃO** desde 18/09 20:29 UTC — confirmado pela URL real, não
só por CI verde: `api.hml` devolve `446aded` no `/health` e serve
`/api/v1/uploads/midia` no openapi; `hml` devolve `d01850b` no `version.json`.

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
| — | Documentação | CHANGELOG + memória + runbook | eu | ✅ | — | — |
| — | **Deploy em hml** | push na `develop` nos 2 repos | eu | ✅ | ✅ `/health` = `446aded`, `/uploads/midia` no openapi | ✅ `version.json` = `d01850b` |

## Decisões desta rodada (confirmadas com o João em 18/09)

1. **Tick de 1 min, tolerância de atraso de 60 s** (era 3 min; João apertou em
   19/09). A tolerância passou a ser do MESMO tamanho do tick — ver o risco de
   offset em segundos em `config.ROTEIRO_ATRASO_MAX_S` e no runbook.
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

---

## Teste contra o WAHA real — 19/09, grupo "Teste" (3 participantes)

Número pareado pelo João na conta de **Luiz Fernando** (user 9). Roteiro **18**
("Rodada 2 — teste de mídia"), execução **12**, campanha 16, grupo **515**
(`120363412181024960@g.us`). Um passo, **5 blocos**, `marcar_todos="sempre"`.

⚠️ Alvo escolhido a dedo: os outros grupos dessa conta têm **800+ participantes
reais** (Promos da Beatriz #1-#4). O passo foi `grupos_alvo="selecao"` com um
id só.

### Medido

| Fato | Valor |
|---|---|
| Tick pegou a execução | `iniciado_em = 14:18:00.032` — **32 ms** depois do minuto agendado |
| Drenagem dos 5 blocos | `enviado_em = 14:18:21.9` → **22 s** no total |
| Folga na tolerância de 60 s | sobraram ~38 s |
| Resultado | execução `concluida`, `enviados=1`, `erros=0`, `pulados=0`, `blocos_enviados=5` |

### O que isso prova

- ✅ **Tick de 1 minuto (088) funciona em hml** — e alinhado ao segundo, que é o
  caso do passo de hora fixa (`HH:MM:00`).
- ✅ **A tolerância de 60 s cabe no caminho normal**: 22 s para 5 blocos, com as
  pausas anti-robô de 2-5 s entre eles.
- ✅ **Os 5 tipos de bloco despacham sem erro**: `sendText`, `sendImage`,
  `sendVideo`, `sendVoice` (com `convert: true`) e `sendFile`. Qualquer 4xx/5xx
  do WAHA teria virado `falhou` — `_classificar_erro_envio` levanta em >= 400.
- ✅ **O commit por bloco funciona**: `blocos_enviados` foi observado em 4 e
  depois 5, que é a retomada da rodada 1 continuando de pé.
- ✅ **`POST /uploads/midia`** subiu os 4 arquivos (png/mp3/mp4/pdf) e as URLs
  voltaram públicas, com o mimetype certo — é delas que o WAHA baixa.

### ✅ Item C resolvido por dado real, sem precisar de teste

A coluna `whatsapp_grupos.descricao` **veio preenchida** com os textos reais de
6 dos 7 grupos depois do primeiro sync pós-deploy. Ou seja: `_descricao_do_payload`
leu a chave certa do payload do GOWS. Era a pendência "qual chave o GOWS usa".

O grupo "Teste" veio com `descricao = ""` (string vazia, não `None`) — e o
código distingue os dois de propósito: vazio é ela ter apagado a descrição,
`None` é o campo não ter vindo.

### ✅ Confirmado no olho, pelo João (19/09)

O banco não responde essas duas — o WAHA aceita a chamada, mas quem decide o
desfecho visual é o WhatsApp, e não existe rota nossa que leia de volta
(`WAHA_URL` é hostname interno do Coolify).

1. **O áudio chegou como BOLHA DE VOZ**, com a onda sonora. Foi enviado um
   **MP3** de propósito (62 KB, 22 kHz mono): o `convert: true` converteu para
   OGG/Opus do lado do WAHA. **Confirma que não precisamos de ffmpeg na nossa
   imagem** — era a alternativa, e custaria ~100 MB na imagem que o VPS puxa.
2. **Só o bloco 1 marcou os 3 participantes**; a legenda da imagem não marcou
   ninguém. Duas coisas de uma vez: `mentions: ["all"]` **funciona no GOWS**
   (o campo escondido do OpenAPI é real), e `_marcar_quem_menciona` limita a
   UM bloco — sem isso o grupo levaria N notificações pelo mesmo passo.

### ✅ O ❓ do documento, respondido: legenda de imagem ACEITA menção

> *"Menção em legenda de imagem varia por versão do Baileys. Corrigir primeiro o
> caso de texto puro; se a legenda não aceitar, restringir o toggle a bloco de
> texto."*

O primeiro teste **não** exercitou isso: o bloco de texto vinha antes e consumia
a menção. Roteiro **19** / execução **14** cobriu o caso de verdade — passo que
**abre com imagem**, sem bloco de texto antes, deixando a legenda como única
âncora possível.

Resultado: **a legenda marcou os 3 participantes**, e o bloco de texto seguinte
não marcou ninguém.

**O plano B não é necessário.** O toggle continua valendo para qualquer bloco
com texto (bloco de texto OU legenda de mídia), como está implementado. E o
GOWS não é Baileys — a ressalva do documento vinha da engine antiga.

## Resumo: as 4 pendências estão fechadas

| # | Pendência | Como fechou |
|---|---|---|
| C | qual chave o GOWS usa para a descrição | **dado real**: 6 grupos vieram com a descrição preenchida no primeiro sync pós-deploy |
| F | `mentions: ["all"]` vira menção no GOWS | **olho**: bloco 1 marcou os 3; só ele |
| F❓ | legenda de imagem aceita menção | **olho**: passo que abre com imagem marcou pela legenda |
| E | `sendVoice` + `convert:true` vira bolha de áudio | **olho**: MP3 chegou como nota de voz com onda sonora |
| H | a tolerância de 60 s cabe | **medido**: tick em 32 ms, 5 blocos em 22 s |

### Resíduo de teste em hml

| Roteiro | Conta | O que é |
|---|---|---|
| 17 "teste (cópia)" | relacionamento@ (user 1), campanha 12 | rascunho, do ciclo duplicar→agendar→cancelar |
| 18 "Rodada 2 — teste de mídia" | Luiz Fernando (user 9), campanha 16 | concluído, 5 blocos no grupo "Teste" |
| 19 "Rodada 2 — menção em legenda" | Luiz Fernando (user 9), campanha 16 | concluído, 2 blocos no grupo "Teste" |

Os 18 e 19 ficam de propósito: são o **registro da execução** que comprova o
teste. Os arquivos de mídia seguem no bucket `images` em
`captures/9/teste-*`.
