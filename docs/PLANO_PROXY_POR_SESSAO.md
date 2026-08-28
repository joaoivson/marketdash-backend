# Plano — Proxy por sessão no WAHA (anti-banimento)

> **STATUS (27/08/2026): implementado.** Backend (migrations 068/069, modelo,
> repositório, `proxy_pool_service`, `waha_client`, motor de envio, sonda,
> rotas admin) + frontend (aba "IPs das conexões" no admin) + 30 testes.
> **Desligado por flag** (`whatsapp_proxy: false`). Pendências reais: o spike
> do §5.1 (o `PUT` + `stop/start` pede novo QR?), a migration 069 em nenhum
> ambiente ainda, e o §7 (aquecimento, spintax, janela) fora de escopo.
> Ver `docs/whatsapp-waha.md` § "Proxy por sessão".
>
> Documento de implementação para ser executado pelo Claude Code no
> `marketdash-backend` (+ um pedaço pequeno no frontend admin).
> Escrito em 27/08/2026. Referência da API: `POST /api/sessions` aceita
> `config.proxy` — confirmado na doc do WAHA (waha.devlike.pro/docs/how-to/proxy).

## 0. A decisão de produto que vem antes do código

**Proxy por sessão é STICKY, não rotativo.** O que derruba número no WhatsApp
não é "ter sempre o mesmo IP" — é **trocar de IP**. Uma sessão que hoje aparece
em São Paulo e daqui a 10 minutos em Frankfurt é o padrão mais óbvio de conta
automatizada que existe. Logo:

- Cada chip fica com **um IP fixo** enquanto estiver saudável.
- "Dinâmico" neste plano = **a alocação** é dinâmica (pool no banco, realoca em
  falha real, admin troca sem redeploy). Não = IP rotativo por mensagem.
- Trocar de proxy é evento **raro e registrado**, com cooldown.

**Afinidade por usuária.** Os 3 chips da mesma afiliada podem (e devem)
compartilhar o mesmo IP — é o retrato coerente de uma pessoa com três aparelhos
na mesma casa. O que NÃO pode é chip de usuárias diferentes dividindo IP: aí um
banimento contamina a vizinhança. Isso também derruba o custo: 100 usuárias =
~100 IPs, não 300.

**Tipo de proxy.** Datacenter (AWS/OVH/Hetzner) é queimado e reconhecido. O
alvo é **residencial ou móvel, BR, com sessão sticky de dias**. Se o orçamento
não permitir para todos, comece pelos chips de maior volume.

## 1. Modelo de dados

### Migration `068_whatsapp_proxies.sql` (seguir o padrão de `067_*`)

```sql
CREATE TABLE whatsapp_proxies (
    id              SERIAL PRIMARY KEY,
    rotulo          VARCHAR(80)  NOT NULL,          -- "BR-móvel-01" (aparece no admin)
    tipo            VARCHAR(16)  NOT NULL,          -- residencial | movel | datacenter
    host            VARCHAR(255) NOT NULL,
    porta           INTEGER      NOT NULL,
    usuario_cifrado TEXT,                           -- app/core/encryption.py (Fernet)
    senha_cifrada   TEXT,
    pais            VARCHAR(2)   NOT NULL DEFAULT 'BR',
    max_sessoes     INTEGER      NOT NULL DEFAULT 3,
    ativo           BOOLEAN      NOT NULL DEFAULT TRUE,
    status          VARCHAR(16)  NOT NULL DEFAULT 'ok',   -- ok | degradado | quarentena
    ultimo_erro     TEXT,
    verificado_em   TIMESTAMPTZ,
    criado_em       TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    atualizado_em   TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

ALTER TABLE whatsapp_instancias
    ADD COLUMN proxy_id       INTEGER REFERENCES whatsapp_proxies(id) ON DELETE SET NULL,
    ADD COLUMN proxy_fixado_em TIMESTAMPTZ,
    ADD COLUMN proxy_trocas    INTEGER NOT NULL DEFAULT 0;

CREATE INDEX idx_whatsapp_instancias_proxy ON whatsapp_instancias(proxy_id);
```

Regras que o schema NÃO expressa e o serviço precisa garantir:

- `max_sessoes` conta apenas instâncias **ativas** apontando para o proxy.
- Um proxy não pode servir duas `user_id` diferentes (afinidade por usuária).
- Credencial **nunca** sai em claro: nem em log, nem em resposta de API, nem em
  mensagem de erro. Mesma regra dos tokens da Shopee (skill `integracoes-marketdash`, princípio 1).

## 2. Backend

### 2.1 `app/models/whatsapp_proxies.py`
Modelo `WhatsappProxy` espelhando a migration + as colunas novas em
`WhatsappInstancia` (`app/models/whatsapp_grupos.py`).

### 2.2 `app/repositories/whatsapp_proxy_repository.py`
- `listar(ativos_apenas: bool)`
- `por_id(id)`
- `contagem_de_sessoes()` → `{proxy_id: n}` em UMA query (`GROUP BY`), não N+1
- `usuarios_por_proxy()` → `{proxy_id: set(user_id)}` para a regra de afinidade
- `criar/atualizar/desativar`

### 2.3 `app/services/proxy_pool_service.py` (núcleo)

```python
def alocar(db, instancia) -> WhatsappProxy | None
```

Ordem de escolha, parando no primeiro que servir:

1. proxy que **já atende outro chip da mesma `user_id`** e tem vaga;
2. proxy `ativo`, `status='ok'`, **sem nenhuma outra usuária**, com vaga,
   preferindo `tipo` móvel/residencial e o de menor ocupação;
3. nenhum → decide por `WHATSAPP_PROXY_OBRIGATORIO`:
   - `true` (produção): levanta `ErroWhatsapp("sem_proxy")`, a tela mostra
     "capacidade de conexão esgotada, fale com o suporte" e **não cria a sessão**;
   - `false` (local/hml): cria sem proxy e loga em `WARNING`.

Outras funções:

- `liberar(db, instancia)` — em `remover()` da instância; zera `proxy_id`.
- `realocar(db, instancia, motivo)` — só por falha de rede/proxy confirmada;
  incrementa `proxy_trocas`, grava motivo, respeita cooldown de 24h e
  **nunca roda no meio de uma fatia de envio** (ver 2.6).
- `credenciais(proxy)` → dict `{"server": f"{host}:{porta}", "username":…, "password":…}`
  já decifrado, montado **só** na hora de chamar o WAHA. `server` vai **sem**
  `http://` (exigência do WAHA).

### 2.4 `app/services/waha_client.py`
- `criar_sessao(webhooks=None, start=True, proxy: dict | None = None)` →
  monta `corpo["config"]["proxy"] = proxy` junto com `webhooks` (hoje o método
  só escreve `config.webhooks`; passar a montar o dict `config` inteiro para não
  sobrescrever um pelo outro).
- `atualizar_sessao(webhooks, proxy=None)` → mesmo cuidado no `PUT`.
- `parar_sessao()` → `POST /api/sessions/{nome}/stop` (não existe hoje; é o que
  permite aplicar proxy novo sem deletar a sessão).
- Nenhum log de `config.proxy` — mascare com `"proxy": "***"` antes de logar.

⚠️ **Verificar em hml antes de codar o resto**: se o `PUT` + `stop/start`
aplica o proxy **sem exigir novo QR**. Se exigir re-pareamento, a troca de proxy
vira operação destrutiva e a regra passa a ser "proxy se define no pareamento e
só muda com re-pareamento agendado" — o que muda a UX do admin, não a estrutura
deste plano. Registre o resultado em `docs/whatsapp-waha.md`.

### 2.5 `app/services/whatsapp_instancia_service.py`
- Em `criar()`: alocar o proxy **antes** de `cliente.criar_sessao(...)` e passar
  adiante. Gravar `proxy_id` + `proxy_fixado_em` na mesma transação da instância
  (proxy alocado e sessão não criada = vaga fantasma no pool).
- Em `qr()`: quando recria a sessão, reusar o **mesmo** proxy já fixado.
- Em `remover()`: `proxy_pool_service.liberar`.
- `sincronizar_eventos` / `sincronizar_todas`: ao dar `PUT` de webhooks,
  reenviar o proxy vigente junto — senão o `config` do WAHA volta sem proxy.

### 2.6 Falha de proxy × banimento (`roteiro_envio_service._tratar_erro`)
Hoje `falhas_seguidas` + `WHATSAPP_FALHAS_PARA_PARAR` tratam tudo igual. Separar:

| Sintoma | Diagnóstico | Ação |
|---|---|---|
| `timeout`/`rede` repetido em **todos** os chips do mesmo proxy | proxy caiu | marca proxy `degradado`, **pausa** a execução, agenda realocação |
| `timeout`/`rede` em **um** chip, proxy ok nos outros | rede pontual | retry normal, não conta como banimento |
| `desconectado`/`auth` do WhatsApp | número caiu ou foi banido | fluxo atual (disjuntor), **não** troca proxy |

O que não se pode fazer: trocar proxy porque o número foi banido. Isso queima o
IP seguinte também.

### 2.7 Sonda de saúde — `app/tasks/proxy_tasks.py`
Task `proxies.verificar` (priority=9), chamada por `pg_cron` via
`/internal/cron/proxy-health` (mesmo desenho dos crons atuais), de hora em hora:

- para cada proxy ativo: `GET https://api.ipify.org` **através do proxy**
  (httpx com `proxies=`), com timeout de 8s;
- 2 falhas seguidas → `status='degradado'`; 4 → `quarentena` + realocação dos
  chips + registro em `sync_runs` (`source="proxy_health"`);
- IP retornado diferente do host esperado é normal em residencial rotativo —
  o que interessa é **país** e estabilidade dentro da janela sticky. Alerta se
  o país sair de BR.

### 2.8 Config (`app/core/config.py`)
```python
WHATSAPP_PROXY_OBRIGATORIO: bool = False     # true em produção
WHATSAPP_PROXY_MAX_SESSOES: int = 3          # default do pool
WHATSAPP_PROXY_COOLDOWN_H: int = 24          # entre trocas do mesmo chip
WHATSAPP_PROXY_HEALTH_TIMEOUT_S: float = 8.0
```

### 2.9 Rotas — `app/api/v1/routes/admin_proxies.py` (admin only)
`GET /admin/proxies` (com ocupação e status), `POST`, `PATCH`, `DELETE`
(soft: `ativo=false`), `POST /admin/proxies/{id}/verificar`,
`POST /admin/instancias/{id}/realocar-proxy`.
Schemas em `app/schemas/whatsapp_proxies.py` — **response nunca inclui
usuário/senha**, só `rotulo`, `tipo`, `pais`, `status`, `ocupacao`.

## 3. Frontend (mínimo)
- `src/features/admin/components/ProxyPoolTab.tsx` — tabela do pool (rótulo,
  tipo, país, ocupação `2/3`, status, último erro, botão *Verificar*), formulário
  de cadastro, e na `WhatsappInstanciaTab` uma coluna "IP" com o rótulo do proxy
  + botão *Realocar* (com confirmação explicando que a sessão reinicia).
- A afiliada **não vê nada disso**. Se `sem_proxy` bloquear a criação, a tela de
  Números mostra a mensagem de capacidade, sem falar em proxy.
- Store em `src/stores/` seguindo o padrão (component → store → service → API).

## 4. Testes (`tests/unit/`)
- `test_proxy_pool.py`: afinidade por usuária; respeito a `max_sessoes`;
  recusa quando `WHATSAPP_PROXY_OBRIGATORIO=true` e pool cheio; `liberar` devolve
  a vaga; cooldown bloqueia realocação precoce.
- `test_waha_client_proxy.py` (MockTransport): `config` sai com `webhooks` **e**
  `proxy` juntos; `server` sem esquema; credencial não aparece em `caplog`.
- `test_whatsapp_instancia_service.py`: falha ao criar sessão no WAHA **não**
  deixa proxy alocado.
- `test_roteiro_envio_*`: falha de rede em todos os chips do mesmo proxy pausa a
  execução; `desconectado` não troca proxy.

## 5. Ordem de execução (PRs pequenos, nesta ordem)
1. **Spike de 1h em hml**: criar uma sessão com `config.proxy`, parear, e testar
   `stop` → `PUT` com outro proxy → `start`. Responder: precisa de QR de novo?
2. Migration 068 + modelo + repositório (sem uso ainda).
3. `proxy_pool_service` + testes (puro, sem rede).
4. `waha_client` (proxy + `parar_sessao`) + testes com MockTransport.
5. Ligação no `whatsapp_instancia_service` atrás de flag em `feature-flags.json`.
6. Sonda de saúde + cron.
7. Admin (backend + frontend).
8. Classificação de erro no motor de envio (2.6).

## 6. Rollout
1. Comprar 2 proxies BR (1 móvel, 1 residencial) e cadastrar no pool de hml.
2. Um chip de teste atrás de proxy por **48h** com envio real em volume baixo
   (10–20 msgs/dia): a métrica é "a sessão não caiu e o número não pediu QR".
3. Chips novos de produção nascem com proxy (`WHATSAPP_PROXY_OBRIGATORIO=true`).
4. Chips já pareados: migrar **em lote pequeno**, um por dia por usuária —
   mudança de IP de um número já ativo é justamente o sinal que queremos evitar,
   então é migração única e definitiva, não passeio.

## 7. O que este plano NÃO resolve (e é o resto do risco de banimento)
Proxy é o IP. O comportamento continua sendo o que mais denuncia robô — mas
**parte disso já está construída** (verificado em 28/08):

Já existe, não reconstrua:
- **Janela de envio** por usuária (`janela_envio_service.py`, 08:00–22:00 com
  pausa, config em `user_settings.whatsapp_envio_config`).
- **Variação de texto** ponderada por mensagem (`template_mensagem_service.sortear_variacao`).
- Ritmo, teto diário por chip e disjuntor de falhas.

Falta:
- **Aquecimento**: chip novo com 80 msgs/dia é convite a banimento. Usar
  `teto_diario` (já existe na tabela) como rampa por dias desde a conexão:
  10 → 20 → 30 → 40. Ver `PLANO_ESCALA_RECOMENDADO.md` Etapa 1.
- **Teto por chip**: 80/dia em grupos é agressivo; 30–50 é a faixa que a maioria
  dos operadores sustenta sem queimar número. Default proposto: 40.
- **Distribuição do disparo**: com 100 afiliadas, campanha livre concentra tudo
  às 9h. Jitter de até 90 min no agendamento evita o pico artificial.
- **Reputação de link**: encurtador próprio repetido em massa é sinalizado —
  alternar domínios de link.

Ordem de retorno pelo esforço: aquecimento > proxy > distribuição do disparo.
