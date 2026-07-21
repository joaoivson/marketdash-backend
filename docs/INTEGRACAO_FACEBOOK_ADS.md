# Integração Facebook Ads — Setup

Integração com a **Facebook Marketing API** para sincronizar campanhas (gasto, cliques,
CPC, CTR, orçamento, status) e permitir **pausar/ativar** e **alterar orçamento** direto
do MarketDash. As comissões/pedidos vêm da Shopee (DatasetRow) via o vínculo manual
campanha → Sub ID.

## 1. Variáveis de ambiente (`.env` do backend)

```env
# App do Facebook (developers.facebook.com)
FACEBOOK_APP_ID=xxxxxxxxxxxxxxxx
FACEBOOK_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FACEBOOK_API_VERSION=v25.0
# Deve ser EXATAMENTE igual à redirect URI registrada no app e usada pelo frontend:
FACEBOOK_OAUTH_REDIRECT_URI=https://app.marketdash.com.br/dashboard/configuracoes
# Login do Facebook para Empresas — ID da configuração (User access token + ads_read/ads_management).
# Substitui o parâmetro `scope` no diálogo OAuth. Sem isso, contas externas em Live falham com "Recurso indisponível".
FACEBOOK_OAUTH_CONFIG_ID=xxxxxxxxxxxxxxxx

# O token de acesso é criptografado com a MESMA chave Fernet do Shopee:
SHOPEE_ENCRYPTION_KEY=<chave Fernet base64 já existente>
```

> O `SHOPEE_ENCRYPTION_KEY` já deve existir (usado pela integração Shopee). O mesmo
> Fernet criptografa o access token do Facebook.

## 2. Criar o App na Meta (passo a passo)

1. Acesse https://developers.facebook.com/ → **Meus Apps** → **Criar App**.
2. Tipo de app: **Empresa (Business)**.
3. No painel do app, adicione o produto **Login do Facebook** e **Marketing API**.
4. Em **Login do Facebook → Configurações**, adicione em *URIs de redirecionamento OAuth válidos*:
   `https://app.marketdash.com.br/dashboard/configuracoes`
   (e a URL de homologação/local que for usar, ex. `http://localhost:8080/dashboard/configuracoes`).
5. Copie **App ID** e **App Secret** (Configurações → Básico) para o `.env`.
6. Permissões necessárias: **`ads_read`** (leitura) e **`ads_management`** (pausar/ativar/orçamento).
7. Em **Login do Facebook para Empresas → Configurações**, crie uma configuração:
   - Tipo de token: **User access token** (não System User)
   - Permissões: `ads_read`, `ads_management`
   - Copie o **`config_id`** gerado para `FACEBOOK_OAUTH_CONFIG_ID`

### App Review (obrigatório para clientes)

- Em **modo de desenvolvimento**, a integração funciona apenas para usuários que são
  **admins/desenvolvedores/testadores** do app. Bom para testar.
- Para clientes reais, é preciso **Business Verification** + **App Review** aprovando
  `ads_read` e `ads_management`. Sem isso, a Meta bloqueia o token para terceiros.

## 3. Sincronização diária (pg_cron) — opcional

A sincronização manual já funciona pelo botão **Sincronizar agora** em Configurações.
Para o sync automático diário, aplique a migration `022_setup_pg_cron_facebook_sync.sql`
no Supabase. Ela reutiliza os secrets do Vault da migration 018
(`cron_shopee_secret` = `CRON_SECRET`, e `backend_base_url`). Roda às **10h30 UTC (7h30 BRT)**.

## 4. Fluxo de uso

1. **Configurações → Integração Facebook → Conectar com Facebook** (OAuth).
2. Selecionar a **conta de anúncio**.
3. **Sincronizar agora** (ou aguardar o cron diário).
4. **Campanhas**: vincular cada campanha ao **Sub ID** da Shopee para ver comissão/lucro/ROAS.

## 5. Endpoints

| Método | Rota | Descrição |
|--------|------|-----------|
| GET | `/api/v1/facebook/oauth-url?redirect_uri=` | URL do diálogo OAuth |
| POST | `/api/v1/facebook/oauth/callback` | Troca code → token long-lived |
| GET | `/api/v1/facebook/ad-accounts` | Lista contas de anúncio |
| PUT | `/api/v1/facebook/ad-account` | Seleciona conta |
| GET | `/api/v1/facebook/status` | Status da integração |
| DELETE | `/api/v1/facebook` | Desconectar |
| POST | `/api/v1/facebook/sync` | Enfileira sync |
| GET | `/api/v1/campaigns` | Lista campanhas + KPIs (filtros: start_date, end_date, only_active, search) |
| GET | `/api/v1/campaigns/{id}` | Detalhe + dia a dia |
| PATCH | `/api/v1/campaigns/{id}/link` | Vincular/desvincular Sub ID |
| PATCH | `/api/v1/campaigns/{id}/status` | Pausar/ativar (escreve no FB) |
| PATCH | `/api/v1/campaigns/{id}/budget` | Alterar orçamento diário (escreve no FB) |

## Observações de produto (a confirmar)

- **Pedidos Diretos**: a regra atual é uma heurística (`channel ILIKE '%direct%'` em
  `DatasetRow`). Confirmar a definição real de "pedido direto" da Shopee.
- **Gasto no Dashboard antigo**: o gasto das campanhas NÃO é espelhado em `AdSpend`
  automaticamente (evita dupla contagem com lançamentos manuais). A tela Campanhas usa
  `CampaignDailyInsight` (Facebook) como fonte do gasto.
