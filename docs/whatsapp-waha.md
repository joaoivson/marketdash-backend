# WhatsApp via WAHA — operação

> Substituiu a Evolution API em 25/08/2026 (decisão registrada no plano do
> Módulo de Grupos): engine **GOWS** (whatsmeow/Go) usa ~60MB por sessão
> contra 300-500MB do Baileys, o WAHA é 100% gratuito desde 2026.6
> (Apache-2.0), e a Evolution 2.4+ passou a exigir ativação com telemetria
> que quebra deploy headless no Coolify.

## O que roda onde

| Ambiente | Como |
|---|---|
| Local | `docker compose --profile whatsapp up waha` (porta **3001**; imagem `devlikeapro/waha:arm` para Mac M-series) |
| Coolify hml | App docker-image `waha-hml` (uuid `hw88gc8ocsko04k8wkocs8kc`), projeto App Backend › homologacao, **servidor `localhost`** — NUNCA o registro "busy" (IP malformado `http:31.97.22.173`; todo deploy nele falha com "Server is not functional") |
| Coolify prod | Criar na promoção, mesmo desenho |

**Configuração real de hml (26/08):** volume `waha-sessions → /app/.sessions`;
"Consistent Container Names" LIGADO (é o que torna o nome do container estável
= uuid do app); a API alcança o WAHA pela rede `coolify` interna:
`WAHA_URL=http://hw88gc8ocsko04k8wkocs8kc:3000` — sem proxy, sem TLS, sem
porta pública. As credenciais (WAHA_API_KEY etc.) vivem SÓ nas envs dos dois
apps no Coolify. Pendência anotada: volume `/app/.media` + env
`WHATSAPP_FILES_FOLDER` quando a F3 começar a mandar mídia.

**Um único servidor WAHA pode atender hml E prod**: as sessões carregam o
prefixo do ambiente no nome (`mkd{ref4-do-banco}...`), e o webhook ignora
sessão de prefixo alheio. Ainda assim, a sessão do RESUMO precisa ser única
por número — o mesmo número pareado em dois lugares derruba a sessão dos dois.

## Variáveis do backend (.env / Coolify)

```
WAHA_URL=            # ex.: http://waha:3000 (mesma rede) ou http://IP:3001
WAHA_API_KEY=        # a MESMA do serviço WAHA (env WAHA_API_KEY lá)
WAHA_SESSAO_RESUMO=  # nome da sessão do número do MarketDash, ex.: mkdytjpresumo
WAHA_WEBHOOK_TOKEN=  # openssl rand -hex 32 — vai como X-Webhook-Token e chave HMAC
WAHA_WEBHOOK_URL=    # https://api.<ambiente>.marketdash.com.br/api/v1/whatsapp/webhook
```

`WAHA_WEBHOOK_URL` é obrigatória em ambiente com proxy: derivar do request
gera `http://`, toma 301 e falha em silêncio (o SAIR não chega — já mordeu).

## Variáveis do serviço WAHA (Coolify)

```
WHATSAPP_DEFAULT_ENGINE=GOWS
WAHA_API_KEY=<a mesma do backend>
WAHA_DASHBOARD_USERNAME=<fixar>
WAHA_DASHBOARD_PASSWORD=<fixar>
WHATSAPP_SWAGGER_USERNAME=<fixar>
WHATSAPP_SWAGGER_PASSWORD=<fixar>
# Persistência de sessão (escolher UMA):
#  a) volume em /app/.sessions (padrão do compose local)
#  b) WHATSAPP_SESSIONS_POSTGRESQL_URL=postgresql://... (recomendado no Coolify)
```

⚠️ **Sem fixar usuário/senha do dashboard, o WAHA REGENERA as credenciais a
cada start** (mesma armadilha das credenciais da Evolution no Coolify —
memória `reference_coolify`). Fixe tudo por env desde o primeiro deploy.

## Engine

- **GOWS** (padrão): whatsmeow/Go. Medido localmente: container com 1 sessão
  ≈ 420MiB (base Node+Go); FAQ oficial: 50 sessões ≈ 1,5 CPU / 3GB.
  O cap `WHATSAPP_MAX_INSTANCIAS_GLOBAL=60` cabe em ~4GB.
- **Fallback: NOWEB** (`WHATSAPP_DEFAULT_ENGINE=NOWEB`) se o GOWS regredir
  (houve memory leaks corrigidos em 2025.12). ⚠️ Payloads de webhook podem
  diferir entre engines — rode os testes de webhook antes de virar a chave.

## Sessões

- **Resumo diário**: sessão única `WAHA_SESSAO_RESUMO`, criada pela tela
  admin (`/admin/sincronizacoes?tab=sistema`), com eventos `message` (SAIR)
  + `session.status`. Parear = escanear o QR na tela do admin.
- **Números das afiliadas**: `mkd{ref4}u{user_id}x{hex4}`, criadas pela tela
  Configurações › WhatsApp › Números, SÓ com evento `session.status` —
  nenhum conteúdo de mensagem chega ao backend (LGPD).
- Webhook único: `POST /api/v1/whatsapp/webhook`, autenticado por
  `X-Webhook-Token`, roteado pelo nome da sessão.

## Migração da Evolution (operacional, uma vez por ambiente)

1. Subir o serviço WAHA no Coolify com as envs acima.
2. Preencher as `WAHA_*` no backend e redeployar a API.
3. Abrir a tela do admin e **escanear o QR de novo** com o número do resumo —
   sessão da Evolution NÃO migra (importar sessão é vetor de sequestro de
   número; decisão consciente de não suportar).
4. Conferir o SAIR: responder "sair" de um número de teste e ver o opt-in
   desligar.
5. Desligar/remover o serviço da Evolution no Coolify.

## Proxy por sessão (anti-banimento)

> Implementado em 27/08/2026 (plano `docs/PLANO_PROXY_POR_SESSAO.md`).
> **Desligado por padrão** — ver a flag abaixo.

**O desenho é STICKY.** Cada chip fica com um IP fixo enquanto estiver
saudável; o que denuncia automação no WhatsApp é TROCAR de IP, não repetir o
mesmo. "Dinâmico" aqui é a **alocação** (pool no banco, realoca em falha real,
admin troca sem redeploy), nunca o IP por mensagem.

**Afinidade por usuária.** Chips da MESMA afiliada dividem IP (retrato coerente
de uma pessoa com três aparelhos em casa, e 1 IP em vez de 3). Chips de
usuárias diferentes, nunca — um banimento contaminaria a vizinhança.

| Peça | Onde |
|---|---|
| Pool + alocação/cooldown | `app/services/proxy_pool_service.py` |
| `config.proxy` no WAHA | `waha_client.criar_sessao/atualizar_sessao` (montam o `config` INTEIRO) |
| Aplicar IP novo numa sessão pareada | `whatsapp_instancia_service.aplicar_proxy_na_sessao` (stop → PUT → start) |
| Sonda de saúde | `app/tasks/proxy_tasks.py` + `POST /api/v1/internal/cron/proxy-health` |
| Admin | `/admin/sincronizacoes?tab=sistema` → "IPs das conexões (proxy)" |
| Migrations | `068_whatsapp_proxies.sql` (tabela) · `069_cron_proxy_health.sql` (pg_cron) |

### Ligar

```
WHATSAPP_PROXY_LIGADO=true       # env (precede o feature-flags.json, sem rebuild)
WHATSAPP_PROXY_OBRIGATORIO=true  # produção: pool cheio ⇒ NÃO cria a sessão
WHATSAPP_PROXY_MAX_SESSOES=3     # default por proxy ao cadastrar
WHATSAPP_PROXY_COOLDOWN_H=24     # entre trocas do MESMO chip
WHATSAPP_PROXY_APLICAR_AUTOMATICO=false   # ver a pendência abaixo
```

Sem `WHATSAPP_PROXY_LIGADO`, vale `whatsapp_proxy` do `feature-flags.json`
(**`true` desde 27/08**). Com a flag ligada e o pool VAZIO + `OBRIGATORIO=true`,
nenhum número novo é criado — cadastre os proxies antes de ligar as duas coisas.

**Flag ligada com pool vazio não faz nada**: cada sessão continua saindo pelo IP
do servidor, agora com um WARNING por criação de número. Para desligar sem
rebuild: `WHATSAPP_PROXY_LIGADO=false` no Coolify + restart.

Ordem para tirar do papel:

1. comprar 2 proxies BR (1 móvel, 1 residencial — datacenter é queimado);
2. cadastrar no admin e clicar em *Verificar* em cada um;
3. aplicar a **migration 069** (antes disso a sonda só geraria `sync_run` vazio
   por hora);
4. números **novos** já nascem com IP; os **já pareados** migram um por dia por
   usuária pelo botão *Realocar* — mudar o IP de um número ativo é justamente o
   sinal a evitar, então é migração única, não passeio;
5. `WHATSAPP_PROXY_OBRIGATORIO=true` só em produção e só com pool com folga.

### ⚠️ Pendência: o `PUT` + `stop/start` pede novo QR?

**Não medido.** O spike §1 do plano (rodar em hml com um proxy real) é o que
responde. Enquanto não houver resposta:

- a realocação automática por quarentena **só mexe no banco** e ALERTA no log;
  a sessão segue no IP antigo até alguém aplicar;
- aplicar na sessão é sempre um clique de gente (admin → *Realocar* → "aplicar
  agora"), com o aviso na tela;
- `WHATSAPP_PROXY_APLICAR_AUTOMATICO=true` só depois de o spike dizer "não
  pede QR". **Registre o resultado aqui.**

Se o WAHA exigir re-pareamento, a regra passa a ser "proxy se define no
pareamento e só muda com re-pareamento agendado" — muda a UX do admin, não a
estrutura do que está implementado.

### Operar

- **Cadastrar**: aba Sistema → *Novo proxy*. Credencial é cifrada (Fernet, a
  mesma `SHOPEE_ENCRYPTION_KEY`) e **nunca** volta em resposta de API nem em log.
- **Sonda**: de hora em hora (migration 069). 2 falhas seguidas → `degradado`;
  4 → `quarentena` (o pool para de alocar nele). Registra em `sync_runs`
  (`source="proxy_health"`).
- **IP diferente do host é normal** em residencial rotativo. O que é alerta é o
  **país** mudar — a sonda loga em ERROR e a tela mostra na verificação manual.
- **Nunca troque de proxy porque o número caiu ou foi banido**: isso queima o
  IP seguinte também. O motor de envio já separa os casos (`timeout`/`rede`
  atrás do mesmo proxy pausa a execução e marca o IP; `desconectado`/`auth`
  segue o disjuntor e não toca no IP).

## Depurar

- Estado da sessão: `GET {WAHA_URL}/api/sessions/{nome}` com `X-Api-Key`.
- Dashboard do WAHA: `{WAHA_URL}/dashboard` (credenciais fixadas por env).
- Evento não chega: conferir `WAHA_WEBHOOK_URL` (https!), o token, e se a
  sessão tem o webhook na config (`GET /api/sessions/{nome}` → config).
- Sessão órfã (existe no WAHA, não existe no banco): reconciliação diária
  entra na F6; até lá, `DELETE /api/sessions/{nome}` manual.
