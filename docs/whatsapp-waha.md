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
| Coolify (hml/prod) | Serviço Docker com `devlikeapro/waha:latest` (amd64) |

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

## Depurar

- Estado da sessão: `GET {WAHA_URL}/api/sessions/{nome}` com `X-Api-Key`.
- Dashboard do WAHA: `{WAHA_URL}/dashboard` (credenciais fixadas por env).
- Evento não chega: conferir `WAHA_WEBHOOK_URL` (https!), o token, e se a
  sessão tem o webhook na config (`GET /api/sessions/{nome}` → config).
- Sessão órfã (existe no WAHA, não existe no banco): reconciliação diária
  entra na F6; até lá, `DELETE /api/sessions/{nome}` manual.
