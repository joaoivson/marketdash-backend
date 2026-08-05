# Diagnóstico IA — design

**Status:** aprovado para implementação · 05/08/2026
**Escopo:** v1 completo, incluindo créditos. Deploy só em `develop` / banco de homologação.

---

## 1. O que é

Um menu novo onde a aluna gera, com um clique, uma análise da operação no período: o que escalar, o que pausar e por quê. Cada geração cria uma **sessão** presa a um retrato dos dados, que produz três coisas ao mesmo tempo:

- um **relatório** estruturado na tela, exportável em PDF;
- uma **conversa** já aberta abaixo, carregada com aquela análise;
- um registro no **histórico**, reabrível a qualquer momento.

Análise atualizada = nova sessão. O PDF é uma foto congelada daquele momento, e está correto que seja.

## 2. Regra de ouro

**A matemática decide, a IA narra.**

O backend classifica cada campanha em verde/amarelo/vermelho pela regra de breakeven que já existe e já bate com o dashboard — `campaign_service._health()`, com `ROAS_BREAKEVEN = 1.0` sobre ROAS Real (comissão líquida ÷ gasto com imposto). A IA recebe os números **já calculados e já classificados** e escreve o texto explicando o porquê e o que fazer.

A IA nunca faz conta e nunca decide sozinha o que está bom ou ruim. Num produto de dados, número alucinado é falha fatal — esta separação elimina a classe inteira do problema.

## 3. Escopo dos dados

O diagnóstico cobre **a operação inteira**, não só campanhas:

- **Sempre:** KPIs do período (comissão líquida, ROAS Real, lucro, pedidos), top canal, top categoria, top sub_id, comparação com o período anterior.
- **Quando houver Meta conectado:** blocos de campanha com `_health` já calculado.

Motivo: hoje 14 de ~29 contas ativas têm Meta. Um diagnóstico exclusivo de campanha nasceria vazio para metade da base. Os KPIs agregados já são calculados pelo dashboard — entram no snapshot sem custo adicional de IA.

Sem Meta conectado, o bloco de campanha simplesmente não existe no snapshot, e o prompt instrui a IA a não mencioná-lo.

## 4. Arquitetura

### 4.1 Geração é síncrona

A chamada à OpenAI leva ~5-15s, o que cabe folgado numa requisição HTTP.

**Não usa Celery.** Decisão informada por incidente real: em 03-04/08 descobrimos que ~50% das tasks de upload sumiam em silêncio porque homologação e produção dividiam a mesma fila do Redis, e o worker do banco errado retornava sem gravar estado. Numa feature que **debita 10 créditos por clique**, sumir em silêncio depois de cobrar é o pior resultado possível.

A fila já foi isolada por banco (`celery_app._fila_do_banco`, commit `df95a71`), mas a lição permanece: trabalho que o usuário está esperando na tela não vai para fila.

**Invariante:** a sessão sempre termina em estado terminal — `pronto` ou `erro`. Nunca fica em `gerando`.

### 4.2 Modelo de dados

```
ai_diagnostics
  id, user_id, periodo_inicio, periodo_fim
  snapshot        JSONB   -- os números congelados
  relatorio       JSONB   -- a saída estruturada da IA
  status          TEXT    -- gerando | pronto | erro
  erro_mensagem   TEXT
  modelo          TEXT    -- ex.: gpt-4o-mini
  tokens_entrada  INT
  tokens_saida    INT
  criado_em, concluido_em

ai_diagnostic_messages
  id, diagnostic_id, papel (user|assistant), conteudo, criado_em

ai_credit_ledger
  id, user_id, diagnostic_id, tipo (geracao|chat), creditos, saldo_apos, criado_em
```

Ledger em vez de contador simples: permite auditar por que uma aluna zerou e medir custo real por conta. Saldo = cota do plano − soma dos débitos do mês corrente.

### 4.3 Fluxo

1. Aluna escolhe o período e clica **Gerar**.
2. Backend valida saldo e adquire lock por usuária (uma geração por vez).
3. Monta o **snapshot** a partir dos serviços existentes (`dashboard_service`, `campaign_service`).
4. Envia à OpenAI com o snapshot já classificado. O prompt de sistema proíbe recalcular.
5. Resposta em JSON estruturado vira o relatório. Sessão → `pronto`. **Débito de 10 créditos só aqui**, no sucesso.
6. Chat abre abaixo, lendo apenas o snapshot congelado.

### 4.4 Camadas

Segue o padrão do projeto: `routes → services → repositories → models`.

- `ai_snapshot_service` — monta o retrato dos dados. Sem IA, testável isolado.
- `ai_diagnostic_service` — orquestra: saldo, lock, snapshot, chamada, persistência.
- `openai_client` — única fronteira com a OpenAI. Timeout, retry, erro tipado.
- `ai_credit_service` — saldo, débito, extrato.

O `openai_client` isolado permite testar todo o resto sem rede.

## 5. Créditos

| Item | Valor |
|---|---|
| Gerar diagnóstico | 10 créditos |
| Mensagem no chat | 1 crédito |
| Cota Pro | 200/mês |
| Cota Max | 1.000/mês |
| Cota Essencial | 0 (menu com cadeado) |

Reset no dia 1º, sem acúmulo. O saldo é derivado do ledger, não guardado em contador — evita divergência.

**Ao zerar:** bloqueia gerar e mostra CTA de upgrade. Sessões antigas continuam legíveis e o chat delas continua funcionando até o teto de mensagens; cortar leitura do que já foi pago seria punitivo.

**Falha da IA não debita.** Cobrar por análise que não veio gera ticket de suporte.

Os números são alavanca comercial, não restrição técnica: mesmo torrando 200 créditos, o custo fica abaixo de R$1/aluna/mês. Podem mudar sem impacto de arquitetura.

## 6. O relatório

Saída estruturada em JSON, renderizada em React:

- **Resumo executivo** — 2-3 frases: saúde geral, ROAS Real do período, destaque e atenção.
- **Escalar** — acima do breakeven, com sugestão de subir orçamento.
- **Pausar** — abaixo de 1,0x, com quanto está perdendo.
- **Observar** — zona cinzenta perto do breakeven.
- **Detalhamento das piores** — motivo de cada uma e custo.
- **Números do período** — fundamentação.
- **Próximos passos** — 2-3 ações práticas.

**PDF:** `window.print()` com CSS de impressão, sem biblioteca no backend. O relatório já está renderizado em React; o PDF sai idêntico ao que ela vê, sem dependência nova nem ~100 MB de WeasyPrint no container.

## 7. O chat

Sem tela em branco: já vem aberto dentro da sessão, com o contexto carregado e 3-4 perguntas sugeridas em botão, geradas a partir do próprio relatório (ex.: "Por que a *kit_cozinha* está no vermelho?").

- Lê **apenas o snapshot congelado** — nunca dados novos. Garante que o chat sempre bata com o PDF.
- Teto de **20 mensagens por sessão**.
- 1 crédito por mensagem da aluna.

## 8. Erros

| Situação | Comportamento |
|---|---|
| OpenAI fora do ar / timeout | Sessão → `erro`, sem débito, mensagem clara e botão de tentar de novo |
| Resposta fora do formato | Uma retentativa; persistindo, `erro` sem débito |
| Sem saldo | 402 com saldo atual e CTA de upgrade |
| Sem dados no período | Bloqueia antes de chamar a IA — não gasta crédito para dizer "não há dados" |
| Geração concorrente | Lock por usuária; segunda tentativa recebe a sessão em andamento |
| Sem `OPENAI_API_KEY` | Menu indisponível com aviso, em vez de erro 500 |

## 9. Acesso

Novo menu `diagnostico_ia` em `core/plans.py`, liberado para Pro e Max, com cadeado no Essencial via `PRO_ONLY_MENUS` — mecanismo que já existe.

## 10. Testes

- `ai_snapshot_service`: monta o snapshot certo com e sem Meta; período sem dados.
- Classificação: o snapshot reflete exatamente o `_health` do `campaign_service` (a IA não pode divergir do dashboard).
- Créditos: débito só no sucesso; saldo derivado do ledger; reset mensal; bloqueio ao zerar.
- Estado terminal: falha da IA nunca deixa sessão em `gerando`.
- `openai_client` mockado em tudo que não for o teste do próprio cliente.

## 11. Fora do escopo

- Resumo diário no WhatsApp → spec própria (v2).
- Automação de Instagram → depende de App Review da Meta.
- Diagnóstico de campanha isolada → o recorte por campanha mora dentro do relatório.
- Streaming da resposta → v2; complica retry, contabilidade de crédito e congelamento.

## 12. Decisões registradas

| Decisão | Alternativa descartada | Motivo |
|---|---|---|
| Geração síncrona | Celery | Fila já provou perder trabalho em silêncio; aqui há crédito debitado |
| Fila por banco (feito) | Índice Redis diferente por ambiente | Sobrevive a `ENVIRONMENT` errado, que é o estado atual dos dois ambientes |
| PDF no frontend | WeasyPrint no backend | Zero dependência; fidelidade ao que a aluna vê |
| Saldo derivado do ledger | Contador na assinatura | Auditável, sem divergência |
| Chat lê só o snapshot | Chat consulta dados frescos | Chat e PDF nunca se contradizem |
| Débito no sucesso | Débito na chamada | Não cobra por análise que não veio |
| `gpt-4o-mini` | Gemini Flash-Lite | Chave OpenAI já disponível; dá conta de PT-BR narrativo e é barato |
