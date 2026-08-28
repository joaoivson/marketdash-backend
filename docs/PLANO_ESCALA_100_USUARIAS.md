# Plano — Degrau C: 100 usuárias ativas (300 chips)

> Escrito em 27/08/2026. Alvo: 100 afiliadas ativas × 3 chips = **300 sessões
> WAHA**, ~24.000 mensagens/dia de pico, dentro de uma janela comercial.
> Complementa `PLANO_PROXY_POR_SESSAO.md` (o IP) — aqui é a máquina.
> Números de capacidade e o porquê dos gargalos: memória `capacidade-infra`.

## 1. Topologia alvo

```
                    ┌─────────────────────────────────────────┐
   pg_cron ────────▶│ VPS-APP  (KVM 4 atual, 4 vCPU / 16 GB)  │
   (Supabase)       │  Coolify · API · worker-web · worker-wpp│
                    │  Redis · frontend                       │
                    └───────┬───────────┬───────────┬─────────┘
                            │           │           │   HTTP interno (rede Coolify
                            ▼           ▼           ▼    ou VPN/Tailscale entre VPS)
                    ┌───────────┐ ┌───────────┐ ┌───────────┐
                    │ WAHA-01   │ │ WAHA-02   │ │ WAHA-03   │  KVM 4 cada
                    │ ~100 sess │ │ ~100 sess │ │ ~100 sess │  (3 vCPU / 7 GB usados)
                    │ + Postgres│ │ + Postgres│ │ + Postgres│  sessões persistidas
                    └─────┬─────┘ └─────┬─────┘ └─────┬─────┘
                          └── proxies BR (1 IP por afiliada) ──┘
                                      │
                    webhook único ────┘  POST /api/v1/whatsapp/webhook
                                         (roteado pelo NOME da sessão — já funciona)
```

**Por que 3 servidores de 100 e não 1 de 300:** raio de incêndio. Hoje, um
container caindo derruba todas as sessões da plataforma; com shards, derruba um
terço — e restart de 100 sessões já é lento o bastante (tempestade de reconexão).
Além disso 300 sessões num só host pedem ~9 vCPU/18 GB, que é servidor grande e
caro, contra três KVM 4 baratos e substituíveis.

**Postgres do WAHA fica no VPS do WAHA**, não no Supabase da aplicação
(`WHATSAPP_SESSIONS_POSTGRESQL_URL` local). Sessão do WhatsApp escreve o tempo
todo; jogar isso no banco do produto é misturar carga crítica com carga de
terceiro.

## 2. Fase 1 — WAHA multi-servidor (o coração da mudança)

Hoje o servidor é **um só**, fixo em `settings.WAHA_URL`. Toda a mudança se
concentra em transformar isso num registro por instância.

### 2.1 Migration `069_waha_servidores.sql`

```sql
CREATE TABLE waha_servidores (
    id             SERIAL PRIMARY KEY,
    rotulo         VARCHAR(60) NOT NULL UNIQUE,   -- "waha-01"
    base_url       VARCHAR(255) NOT NULL,         -- http://waha01:3000 (rede interna)
    api_key_cifrada TEXT NOT NULL,                -- app/core/encryption.py
    max_sessoes    INTEGER NOT NULL DEFAULT 100,
    ativo          BOOLEAN NOT NULL DEFAULT TRUE,
    aceita_novas   BOOLEAN NOT NULL DEFAULT TRUE, -- drenar sem desligar
    status         VARCHAR(16) NOT NULL DEFAULT 'ok',
    verificado_em  TIMESTAMPTZ,
    criado_em      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

ALTER TABLE whatsapp_instancias
    ADD COLUMN servidor_id INTEGER REFERENCES waha_servidores(id);

CREATE INDEX idx_whatsapp_instancias_servidor ON whatsapp_instancias(servidor_id);
```

Backfill: criar a linha do servidor atual a partir das envs e apontar **todas**
as instâncias existentes para ela. Sem isso, o deploy quebra as sessões vivas.

### 2.2 `cliente_da_sessao` vira resolvedor (ponto único)

`app/services/whatsapp_instancia_service.py:63` é o gargalo feliz desta
mudança — quase todo mundo passa por ele:

```python
def cliente_da_sessao(nome_instancia: str) -> WahaClient:
    servidor = resolver_servidor(nome_instancia)     # cache in-process, TTL 60s
    return WahaClient(servidor.base_url, servidor.api_key, nome_instancia)
```

`resolver_servidor` consulta `whatsapp_instancias.nome_instancia` (já é UNIQUE)
com **cache em memória**: sem cache vira uma query por mensagem enviada. Fallback
para `settings.WAHA_URL` quando a instância não tem `servidor_id` (transição).

Os **3 call-sites que não passam por ele** e precisam de ajuste explícito:

| Arquivo | Hoje | Vira |
|---|---|---|
| `routes/whatsapp.py:45` (`_cliente_resumo`) | `settings.WAHA_URL` | servidor marcado como "sistema" (a sessão do resumo é única e fixa) |
| `services/whatsapp_runner.py:27` | idem | idem |
| `services/grupo_snapshot_service.py:66` (reconciliar órfãs) | lista sessões de UM servidor | **itera todos os servidores ativos** — órfã em shard não varrido vive para sempre consumindo RAM |

### 2.3 Alocação de servidor
Mesma regra de afinidade do proxy: **os 3 chips da mesma afiliada no mesmo
servidor** (e no mesmo IP). Facilita debug, concentra o estrago e simplifica a
conta de capacidade. Escolha = servidor `ativo`, `aceita_novas`, com vaga, menor
ocupação.

⚠️ **Sessão não migra de servidor sem re-parear** — o estado do whatsmeow vive
no Postgres daquele WAHA. Logo a alocação é *definitiva*: para esvaziar um
servidor, marque `aceita_novas=false` e espere a rotatividade natural, ou
re-pareie com aviso à afiliada.

### 2.4 Cap global
`WHATSAPP_MAX_INSTANCIAS_GLOBAL=60` deixa de ser constante: passa a ser
`SUM(max_sessoes)` dos servidores ativos, com o env virando apenas um teto de
segurança. `LimiteGlobal` continua sendo a exceção lançada.

### 2.5 Rede entre os VPS
Os WAHA **não podem** ter porta pública. Duas opções, nessa ordem de preferência:
1. os 3 WAHA no mesmo Coolify/rede interna, se o provedor oferecer rede privada;
2. VPN leve (Tailscale/WireGuard) entre VPS-APP e os WAHA, `base_url` na IP da VPN.
Nunca `http://IP:3000` exposto — a `X-Api-Key` do WAHA é a chave de 100 números.

## 3. Fase 2 — Vazão de envio (Celery)

**A conta**: 24.000 msgs/dia numa janela de 6h = 1,1 msg/s. Cada execução
entrega ~0,125 msg/s (rodada de 2 + pausa 8–20s). Logo: **~9 execuções
simultâneas** em regime, e ~27 no pico se todas as campanhas caírem na mesma
faixa. Hoje há **4 slots**, compartilhados com CSV, Shopee e Facebook.

### 3.1 Fila e worker dedicados
- Fila `marketdash-{banco}-whatsapp`; `roteiros.processar_execucao` e as tasks
  de grupo passam a rotear para ela (`task_routes` no `celery_app.py`).
- Worker próprio: `celery -A app.tasks.celery_app worker -Q marketdash-{banco}-whatsapp`.
- **Concorrência**: as tasks só dormem — não precisam de CPU. Duas saídas:
  - **prefork, 4 containers × 8 = 32 slots** — chato de operar, zero risco novo;
  - **gevent, `--pool gevent --concurrency 64`** — 1 processo, muito mais barato.
    ⚠️ exige `psycogreen` (monkey-patch do psycopg2), senão **cada query bloqueia
    o hub inteiro** e a concorrência vira teatro. `httpx` síncrono funciona com o
    socket patchado. Testar sob carga antes de virar a chave em produção.
- Recomendação: começar prefork (2 containers × 8 = 16 slots, cobre o regime),
  medir, e só ir para gevent se o pico exigir.

### 3.2 Pool de banco por worker
`pool_size=5, max_overflow=5` **por processo**. Com 4 containers isso é 40
conexões só de worker. Baixar para `pool_size=2, max_overflow=3` nos workers e
manter o pooler de transação (`:6543`, já em uso) — sem isso o Supabase estoura
antes do WhatsApp.

### 3.3 Tetos
`WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA`: 5.000 → **30.000**, com alarme em 80%.
`WHATSAPP_TETO_POR_INSTANCIA`: manter em 80 só para chip aquecido — o default de
chip novo passa a ser a rampa de aquecimento (`PLANO_PROXY_POR_SESSAO.md` §7).

### 3.4 Janela de envio
Com 100 afiliadas, disparo livre concentra tudo às 9h. Distribuir: a execução
ganha uma janela preferida por usuária (ou um jitter de até 90 min no
agendamento), senão o pico exige 3× a infra do regime.

## 4. Fase 3 — Banco (Supabase)

1. **Antes de subir compute**: `pg_stat_statements` / o próprio assistente do
   painel para as 10 queries mais caras. Compute maior mascarando query ruim é
   custo eterno.
2. **Medium (4 GB)** como alvo. Small (2 GB) provavelmente segura o regime, mas
   não deixa folga para o dashboard + CSV + Shopee no mesmo horário.
3. **Retenção**: `roteiro_mensagens` a 24k linhas/dia = ~8,7M linhas/ano. Definir
   agora: partição mensal ou arquivamento de >90 dias (agregados ficam, linha
   crua vai para tabela histórica/S3). Fazer isso com a tabela pequena é barato;
   com 8M linhas, é uma janela de manutenção.
4. **Índices** a conferir com o volume novo: `(user_id, criado_em)` nas tabelas
   de mensagem/evento e `(instancia_id, dia)` nos contadores de teto diário —
   essa última roda a cada mensagem enviada.
5. **Backup**: com 100 clientes pagantes, backup diário do Supabase é o mínimo;
   o VPS está em backup semanal (checar no painel Hostinger).

## 5. Fase 4 — IP e comportamento
Ver `PLANO_PROXY_POR_SESSAO.md`. Resumo do que o degrau C exige:
~100 IPs BR residenciais/móveis (1 por afiliada, sticky), aquecimento
automático de chip novo, variação de texto e janela humana. **Esta é a fase que
decide se o produto sobrevive** — servidor a mais não salva número banido.

## 6. Fase 5 — Operação

- **Métricas mínimas** (painel admin ou Sentry/Grafana): sessões `WORKING` por
  servidor, msgs/hora, taxa de falha por chip, chips fora do ar > 10 min,
  tamanho da fila `whatsapp`, proxies degradados.
- **Reconciliação diária** de sessões órfãs — agora por servidor (§2.2).
- **Deploy do WAHA é evento de risco**: 100 sessões reconectando ao mesmo tempo.
  Nunca em janela de envio; `Consistent Container Names` ligado; um shard por vez.
- **Runbook de shard morto**: como marcar `ativo=false`, o que a afiliada vê
  (número "reconectando"), e o roteiro de re-pareamento.

## 7. Ordem de execução e marcos

| Marco | Entrega | Capacidade |
|---|---|---|
| M0 | hoje | ~20 usuárias |
| M1 | fila + worker dedicado (§3.1 prefork) + pool (§3.2) + Supabase Small | ~35–40 |
| M2 | multi-servidor (§2) + WAHA-02 dedicado | ~60 |
| M3 | proxies + aquecimento (`PLANO_PROXY_POR_SESSAO.md`) | 60 com número sobrevivendo |
| M4 | WAHA-03 + Supabase Medium + retenção (§4.3) + observabilidade | **100** |

M1 e M3 são os que mais rendem por real gasto. M2 é o de maior risco técnico
(mexe no ponto por onde passa todo envio) — merece PR próprio, testes com
MockTransport em dois servidores e deploy em hml antes.

## 8. Custo — o que cotar
- 2 VPS adicionais (mesma classe KVM 4) — previsível.
- Supabase nano → Medium — previsível.
- **~100 proxies BR residenciais/móveis: é o item dominante e o mais variável.**
  A afinidade por usuária (§2.3) já corta o custo em 3× frente a 1 IP por chip.
  Cotar antes de fechar o preço do plano — proxy é custo recorrente por cliente
  ativo e precisa caber na margem, não na esperança.
