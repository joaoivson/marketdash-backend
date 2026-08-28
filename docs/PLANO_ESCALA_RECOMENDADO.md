# Plano recomendado — caminho até 100 usuárias ativas

> 28/08/2026. **Este é o documento de execução**: leia-o primeiro e siga a ordem.
> Os outros dois são detalhamento chamado por etapa —
> `PLANO_PROXY_POR_SESSAO.md` (IP) e `PLANO_ESCALA_100_USUARIAS.md` (arquitetura
> de 300 sessões). Capacidade e gargalos medidos: memória `capacidade-infra`.

## A recomendação em uma frase

**Não construa a infra de 100 usuárias agora. Construa a alavanca que permite
chegar lá — e compre capacidade conforme a adoção**, porque código de
multi-servidor se escreve uma vez e VPS/proxy se paga todo mês, com ou sem
cliente em cima.

O módulo de grupos tem dias de produção. Comprar 3 shards hoje é pagar por
capacidade ociosa e operar arquitetura distribuída sem observabilidade.

## Estado real do código (verificado em 28/08, não presuma o contrário)

Já existe e **não precisa ser construído**:

- **Janela de envio por usuária** — `janela_envio_service.py`, padrão 08:00–22:00
  com pausa, config em `user_settings.whatsapp_envio_config`.
- **Variação de texto ponderada** — `template_mensagem_service.sortear_variacao`,
  sorteio por mensagem no disparo.
- **Ritmo, teto diário, disjuntor** — `roteiro_envio_service` + `config.py`.
- **Flag `whatsapp_proxy: false`** já em `feature-flags.json` (raiz).

Não existe: **aquecimento de chip novo**, **proxy por sessão**, **WAHA
multi-servidor**, **fila/worker dedicados de WhatsApp**.

## Regras para quem executar (agente ou humano)

1. **Um PR por etapa.** Etapa que mistura infra e regra de negócio não tem como
   ser revertida sozinha.
2. **Migration é SQL numerado em `migrations/`** (a última é `067_`), aplicada à
   mão — não há Alembic nem tabela de aplicadas (débito conhecido). Escreva
   idempotente (`IF NOT EXISTS`) e diga no PR a ordem de aplicação.
3. **Teste antes do deploy**: `pytest tests/ -v`. Regra de negócio nova sem teste
   unitário não entra.
4. **hml antes de produção, sempre.** Nada que toque sessão viva vai direto.
5. **Nada de credencial em log**, nem truncada — vale para proxy e API key de
   servidor WAHA (princípio 1 da skill `integracoes-marketdash`).
6. **Celery só priority 0 ou 9.** Nunca 5 (ver `DECISOES.md`).
7. Ao terminar cada etapa, **registre a decisão** em `.claude/memoria/DECISOES.md`
   no formato da tabela de lá.

---

## Etapa 0 — Medir antes de dimensionar (meio dia)

Todo o dimensionamento até aqui vem do FAQ do WAHA, **não do servidor de vocês**.

**Faça:** suba 10 sessões reais em hml e meça `docker stats` do container WAHA em
regime (1h), mais o pico durante o pareamento. Registre RAM/sessão, CPU/sessão e
o tempo de restart com 10 sessões.

**Critério de aceite:** os números reais escritos em `docs/whatsapp-waha.md`,
substituindo as estimativas do FAQ.

**Por quê primeiro:** se der 40 MB/sessão em vez de 60, muda o número de shards;
se der 120, muda o orçamento inteiro. Meia tarde calibra uma decisão de milhares
de reais.

---

## Etapa 1 — Proteger o ativo (o número) — 3 a 5 dias

O que queima primeiro não é CPU, é chip. Duas mudanças:

### 1.1 Teto de 80 → 40 msgs/chip/dia
`WHATSAPP_TETO_POR_INSTANCIA: int = 40` em `config.py`.

Isto é **duas decisões numa linha**: protege o número (80/dia em grupos queima
chip novo) e **corta a infra pela metade** — 100 usuárias × 3 chips × 40 =
12.000 msgs/dia em vez de 24.000. Metade da vazão, metade do worker, metade da
pressão no banco. 80 continua disponível por chip via `teto_diario`, para número
maduro, decidido no admin.

### 1.2 Aquecimento de chip novo (a peça que falta)
`whatsapp_instancias.teto_diario` já existe e hoje nasce `NULL` (= default do
sistema). Passe a preencher como **rampa a partir da data de conexão**:

| Dias desde a 1ª conexão | Teto |
|---|---|
| 0–2 | 10 |
| 3–6 | 20 |
| 7–13 | 30 |
| 14+ | 40 (default do sistema) |

Implementação: função pura `teto_do_dia(instancia, hoje)` em
`whatsapp_instancia_service.py`, usada por `_instancias_elegiveis`
(`roteiro_envio_service.py:176`) — **não** grave a rampa no banco; calcule.
Assim mudar a curva é uma linha, não uma migração de dados. Migration só para
`primeira_conexao_em TIMESTAMPTZ` (backfill = `ultima_conexao_em` das atuais).

**Testes:** `tests/unit/test_aquecimento.py` — cada faixa da rampa; chip
reconectado não reinicia a rampa; `teto_diario` manual do admin **vence** a rampa.

**Critério de aceite:** chip criado hoje envia no máximo 10 mensagens; chip com
20 dias envia 40.

**Não faça:** rampa por número de mensagens enviadas em vez de dias — chip que
ficou parado voltaria a "aquecer" sozinho.

---

## Etapa 2 — Vazão: fila e worker dedicados (2 a 3 dias)

Hoje há **4 slots** de Celery disputados por CSV, Shopee, Facebook e WhatsApp, e
`processar_fatia` dorme até 900s segurando um deles.

**Faça:**
1. `task_routes` no `celery_app.py`: `roteiros.*` e tasks de grupo →
   fila `marketdash-{identidade_do_banco()}-whatsapp` (derivada do banco, como a
   atual — ver `_fila_do_banco`).
2. Container worker novo no Coolify com `-Q <fila-whatsapp>`, **prefork,
   `--concurrency 8`**, 2 réplicas = 16 slots.
3. Pool do banco menor **nos workers**: `pool_size=2, max_overflow=3`
   (`app/db/session.py`, via env) — 4 processos × 10 conexões estoura o Supabase.
4. `WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA`: 5.000 → **15.000**, com log de alerta em
   80%.
5. Supabase: **nano → Small**. Antes, olhe as 10 queries mais caras
   (`pg_stat_statements` / assistente do painel) — compute maior mascarando query
   ruim é custo eterno.

**Critério de aceite:** 10 execuções simultâneas em hml andando de verdade
(medir msgs/hora), sem upload de CSV ficar preso atrás delas.

**Não faça agora:** gevent. É mais barato, mas exige `psycogreen` (sem ele cada
query bloqueia o hub e a concorrência vira teatro). Só depois de medir o pico
real com prefork.

**Capacidade após esta etapa: ~35–40 usuárias.**

---

## Etapa 3 — Piloto de proxy (1 semana, 2 IPs)

Detalhe completo: `PLANO_PROXY_POR_SESSAO.md`. Aqui, o recorte mínimo:

1. **Spike (1h)**: sessão em hml com `config.proxy`; testar `stop` → `PUT` com
   outro proxy → `start`. **Responder: exige QR de novo?** A resposta define se
   trocar proxy é operação corriqueira ou destrutiva. Registrar em
   `docs/whatsapp-waha.md`.
2. Migration 068 + pool + alocação com **afinidade por usuária** (os 3 chips da
   mesma afiliada no mesmo IP; nunca IPs compartilhados entre usuárias).
3. Ligar atrás da flag `whatsapp_proxy` que **já existe** no `feature-flags.json`.
4. **2 proxies BR** (1 móvel, 1 residencial), 1 chip real, 48h em volume baixo.
   Métrica: a sessão não caiu e o número não pediu QR.

**Não faça:** comprar 100 proxies antes do piloto; rotação de IP por mensagem
(é o padrão que mais denuncia robô — o proxy é sticky por chip).

---

## Etapa 4 — Abstração multi-servidor + 1 WAHA dedicado (1 semana)

Detalhe: `PLANO_ESCALA_100_USUARIAS.md` §2. **Escreva a abstração antes de
precisar dela** — feito isso, adicionar capacidade vira "sobe container, insere
linha", sem deploy e sem refatoração sob pressão.

**Faça:** migration `069_waha_servidores.sql` + `servidor_id` na instância;
`cliente_da_sessao()` resolvendo o servidor pelo nome da sessão **com cache
in-process (TTL 60s)**; os 3 call-sites que escapam dela
(`routes/whatsapp.py:45`, `whatsapp_runner.py:27`, `grupo_snapshot_service.py:66`
— este passa a **iterar todos os servidores**); cap global vira
`SUM(max_sessoes)` dos servidores ativos. Backfill apontando todas as instâncias
vivas para o servidor atual — sem isso o deploy derruba as sessões pareadas.

**Depois disso**, e só depois, suba **um** VPS dedicado ao WAHA e mova as
sessões novas para lá. Sessão **não migra** de servidor sem re-parear: use
`aceita_novas=false` no servidor antigo e deixe a rotatividade fazer o trabalho.

**Critério de aceite:** duas sessões em servidores diferentes, enviando, com
webhook chegando das duas; teste com MockTransport cobrindo dois servidores.

**Capacidade após esta etapa: ~60 usuárias.**

---

## Etapa 5 — Escalar por adoção (contínuo)

Não é uma etapa de código: é o gatilho de compra. Adicione capacidade **quando o
número for atingido**, não antes:

| Gatilho | Ação |
|---|---|
| Sessões ativas > 70% do `max_sessoes` do pool | subir mais um VPS WAHA (~1 por 30 usuárias) |
| Fila `whatsapp` com espera > 15 min no pico | +1 réplica de worker (ou avaliar gevent) |
| Supabase RAM > 70% ou disco > 60% | Small → Medium |
| `roteiro_mensagens` > 2M linhas | executar a retenção (partição/arquivo >90d) |
| Proxies em `degradado` > 10% | rever fornecedor antes de comprar mais |

**Retenção decidida agora, executada cedo:** a 12k msgs/dia são ~4,4M linhas/ano
em `roteiro_mensagens`. Particionar com a tabela pequena é barato; com 4M linhas
é janela de manutenção.

**Separe hml de produção** antes dos primeiros 50 clientes pagantes: hoje os dois
dividem VPS e podem dividir servidor WAHA — um deploy de homologação não pode
chegar perto de sessão de cliente.

---

## A pergunta que decide o desenho comercial

Proxy é **custo recorrente por cliente ativo**. Antes de vender 100 assinaturas,
calcule o custo real de uma usuária com 3 chips (proxy + fatia de VPS + banco) e
confira se cabe na margem. Se não couber, a resposta não é engenharia: é cobrar
os 3 chips num tier mais caro, ou entregar 1 chip no plano de entrada.

Esse número sai da Etapa 0 (custo de infra por sessão) + da Etapa 3 (preço real
do proxy). **Faça a conta antes da Etapa 4**, porque ela muda o que vale a pena
construir.

## Ordem final, sem rodeios

```
0. medir (½ dia)            → calibra tudo
1. teto 40 + aquecimento    → protege o ativo, corta a infra pela metade
2. fila + worker + Small    → 35–40 usuárias, sem servidor novo
3. piloto de proxy (2 IPs)  → responde se a operação é viável
    ↳ conta de custo por usuária ativa
4. multi-servidor + 1 WAHA  → ~60 usuárias, capacidade vira parafuso
5. escalar por gatilho      → 100
```
