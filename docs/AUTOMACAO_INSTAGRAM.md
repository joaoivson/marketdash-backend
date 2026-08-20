# Automação Instagram — Comentário → Direct

Implementação do spec v1 (Luiz Fernando, 19/08/2026). A aluna publica um post
"Comente QUERO para receber o link"; quando alguém comenta a palavra, o MarketDash
responde publicamente (opcional) e manda o link no direct.

**Nada além disso.** Os itens do "fora do escopo v1" do spec não foram
implementados nem deixados "preparados": não há construtor de fluxo, mensagem de
boas-vindas, follow-up, pedido de seguir, captura de e-mail, IA, automação por
story/mention/DM, Live, nem qualquer marca d'água na mensagem. O texto que sai é
exatamente o que a aluna escreveu.

---

## 1. Arquitetura da conexão

**Business Login for Instagram** (`graph.instagram.com`), com credenciais próprias
dentro do mesmo app da Meta. **Não** usa Facebook Login for Business.

| | Meta Ads (existente) | Instagram (novo) |
|---|---|---|
| Login | Facebook Login for Business (`config_id 2527001584409306`) | Business Login for Instagram |
| Host | `graph.facebook.com` | `graph.instagram.com` |
| Credenciais | `FACEBOOK_APP_ID/SECRET` | `INSTAGRAM_APP_ID/SECRET` |
| Exige Página do Facebook | Sim | **Não** |
| Token | User token do Facebook | Instagram User access token |

Motivos, na ordem em que importam:

1. Não exige Página do Facebook vinculada — o caminho via Facebook exige Página e
   cargo de admin nela, atrito que o público não passa.
2. Não encosta na configuração de anúncios que já funciona.
3. Isolamento de risco: permissão do Instagram revogada não derruba o Meta Ads.

Resultado: **duas conexões independentes** em Configurações — "Meta Ads"
(existente) e "Instagram" (nova).

O diálogo de autorização usa `force_reauth=true`. Documentado como *"forces an app
user to use their Instagram professional account credentials to log into your app
even if the user is logged into Instagram"* — sem isso, a Meta nem mostra tela de
login quando já existe sessão no navegador, e a aluna conectaria a conta que
estiver logada ali, que pode não ser a dela. O custo é um login a mais; o
benefício é não ter direct saindo do perfil errado.

---

## 2. Configuração no App Dashboard

> **Confirmado no painel em 19/08/2026.** O caso de uso Instagram tem credenciais
> PRÓPRIAS, que não são o App ID do Facebook — a discussão sobre `...338...` × `...348...`
> não se aplica aqui:
>
> | | Valor |
> |---|---|
> | Nome do app do Instagram | `MarketDash-IG` |
> | **ID do app do Instagram** (`INSTAGRAM_APP_ID`) | `1041324905491556` |
> | Chave secreta (`INSTAGRAM_APP_SECRET`) | no `.env`, **nunca neste documento** |
>
> Painel: **API do Instagram → Configuração da API com login do Instagram**. Note que
> o menu tem DUAS entradas parecidas ("com login do Instagram" e "com login do
> Facebook") — a nossa é a **do Instagram**. A tela mostra o aviso *"Se quiser
> rastrear hashtags e insights, troque para o API setup with Facebook login"*, que é
> exatamente o caminho que decidimos NÃO usar (§1).

1. Adicionar o caso de uso **Instagram** → seção **API setup with Instagram login**.
2. Copiar **Instagram app ID** e **Instagram app secret** (≠ Meta App ID) para
   `INSTAGRAM_APP_ID` / `INSTAGRAM_APP_SECRET`.
3. **Redirect URL** (OAuth de retorno):
   - HML: `https://hml.marketdash.com.br/dashboard/automacoes/callback`
   - PROD: `https://marketdash.com.br/dashboard/automacoes/callback`

   **Desenvolvimento local exige túnel HTTPS.** A doc do Business Login for
   Instagram não traz uma frase exigindo HTTPS na Redirect URI — mas também **não
   documenta nenhuma exceção para `http://localhost`**, e todos os exemplos oficiais
   são HTTPS (a exceção folclórica de localhost é de fórum, não de doc). Some-se a
   isso que o webhook exige HTTPS com TLS válido (texto oficial) e que o callback de
   exclusão de dados exige HTTPS explicitamente. Conclusão prática: para rodar o
   fluxo na máquina, use ngrok/Cloudflare Tunnel e cadastre a URL do túnel como
   Redirect URL. Se o painel aceitar `http://localhost:8080/...`, ótimo — mas não
   planeje o dia de trabalho contando com isso.
4. **Deauthorization callback URL**: `https://api.hml.marketdash.com.br/webhooks/instagram/deauthorize`
5. **Data deletion request URL**: `https://api.hml.marketdash.com.br/webhooks/instagram/data-deletion`
6. **Configure webhooks** → Callback URL `https://api.hml.marketdash.com.br/webhooks/instagram`,
   Verify token = `INSTAGRAM_WEBHOOK_VERIFY_TOKEN` → assinar o campo **`comments`**.

> **Um app tem UMA callback URL de webhook.** Redirect URI aceita várias; webhook,
> não — a configuração é um único par Callback URL + Verify token no painel, e a
> doc da Graph API é explícita em que *"Webhooks for Instagram is not supported"*
> via API (só pelo App Dashboard), então nem dá para contornar criando uma segunda
> subscription. **HML e PROD não recebem comentário real ao mesmo tempo no mesmo app.**
> Ver §2.1 para a decisão.

### 2.1 HML e PROD: um app ou dois?

**Opção A — um app só (recomendada para começar).** Aponta o webhook para HML,
valida tudo, e na promoção troca a URL para produção. Depois disso, teste em HML
passa a ser pelo botão **Test** do painel e por
`scripts/simular_comentario_instagram.py` — o que, dado que o webhook `comments`
já não dispara sem Advanced Access, é o caminho que você usaria de qualquer jeito
antes do review. Custo: zero.

**Opção B — um segundo app da Meta só para homologação.** HML segue recebendo
comentário real depois do lançamento. Herda a Business Verification do portfólio
e, como só admin/testador conecta, não precisa de App Review próprio. Custo: um
segundo par de credenciais — que o código já suporta sem nenhuma alteração, porque
`INSTAGRAM_APP_ID`/`INSTAGRAM_APP_SECRET` são variáveis por ambiente. Mais
trabalho de painel, ambiente mais limpo.

Não há terceira opção decente: espelhar o webhook de produção para HML no backend
introduz um caminho de dados que ninguém audita, para um ganho que o script de
simulação já entrega.

> A URL de callback do OAuth é uma rota PRÓPRIA (`/dashboard/automacoes/callback`)
> e não a tela de Configurações. A tela de Configurações lê `?code` da URL para
> concluir o OAuth do **Facebook**; se o Instagram voltasse no mesmo endereço, um
> consumiria o `code` do outro e as duas conexões quebrariam.

### Adicionar a conta de teste no app (passo que parece bug se for pulado)

**App Dashboard → Instagram → API setup with Instagram login → seção "Generate
access tokens" → Add account**, e fazer login na conta profissional do teste.

Em Standard Access **só conectam contas adicionadas ali** — pular esse passo faz o
OAuth falhar de um jeito que parece bug de código. A conta precisa estar **pública**
(a doc é explícita: *"This account must be public"*). Dá para adicionar várias, uma
por testador. Elas também aparecem em **App Roles → Roles**.

### Os 4 passos do webhook — e o passo 3, que quase ficou de fora

A Meta lista quatro passos obrigatórios. O terceiro é o que mais some das
implementações:

| # | Passo | Onde |
|---|---|---|
| 1 | Endpoint que recebe o webhook | código (`/webhooks/instagram`) |
| 2 | Assinar o campo `comments` | **painel**, vale para o APP |
| 3 | **Habilitar a CONTA da aluna a receber notificação** | **chamada de API, por conta** |
| 4 | Testar | comentário real ou script de simulação |

O passo 3 é `POST https://graph.instagram.com/<versão>/<IG_ID>/subscribed_apps?subscribed_fields=comments&access_token=<token da conta>`,
resposta `{"success": true}`.

**Sem ele o webhook nunca dispara — e não há erro em canto nenhum.** O OAuth
funciona, a tela funciona, a automação salva, e nada acontece. É a falha mais cara
da integração justamente porque é silenciosa.

Onde isso roda no código (`instagram_connection_service.assinar_webhook`):

- **no fim do OAuth**, logo depois de gravar o token;
- **de novo a cada renovação** de token (a doc não diz que a inscrição cai junto
  com o token antigo, mas também não garante que sobrevive — e reinscrever é
  idempotente e barato);
- **sob demanda**, pelo botão "Tentar de novo" da tela, via
  `POST /api/v1/instagram/connection/subscribe`.

O resultado fica em `instagram_connections.webhook_subscrito` / `webhook_erro`, e a
tela mostra um alerta âmbar enquanto for `false`: *"Conectado, mas ainda não
estamos recebendo os comentários"*.

> **Por que a falha na inscrição NÃO bloqueia a conexão.** A doc da Meta não diz se
> `subscribed_apps` funciona com o app em Development mode. Se não funcionar,
> bloquear tornaria impossível conectar antes do App Review — exatamente quando a
> gente precisa conectar para homologar. Então gravamos a conexão, marcamos o
> problema e deixamos visível, com retentativa.

> **O ID certo é `user_id`, não `id`.** `GET /me` devolve os dois: `id` é
> app-scoped e `user_id` é o da conta profissional — e é o `user_id` que chega como
> `entry.id` no webhook. Inscrever com o `id` errado faz a Meta aceitar a chamada e
> o webhook nunca casar com nenhuma conexão nossa. O código já usa `user_id`, com
> teste de regressão.

### Automação não fica ativa com o webhook fora do ar

Ativar uma automação exige, além da conexão viva, `webhook_subscrito = true`. Se
faltar, o backend **tenta inscrever na hora** e só recusa se não conseguir — 409
`WEBHOOK_NAO_ATIVO`, com o motivo. Na lista, o toggle fica travado e aparece o selo
"Aguardando conexão"; **pausar continua sempre liberado**.

Por que travar: sem isso a aluna publica o post achando que está rodando e descobre
pelo silêncio. Automação "ativa" que não dispara é pior que automação pausada.

### Escopos: guardamos o que foi CONCEDIDO, não o que pedimos

A tela de consentimento da Meta agrupa permissões com nomes próprios — dá para sair
de lá sem `instagram_business_manage_comments` sem perceber. Nesse caso o direct
continua saindo e **só a resposta pública para de funcionar**, sem erro nenhum.

Por isso `instagram_connections.scopes` recebe a lista que veio na resposta do OAuth
(campo `permissions`), não a lista que pedimos. Faltando o escopo de comentários, a
tela avisa. Se a Meta não devolver `permissions`, caímos no que pedimos e não
alarmamos à toa.

Conferência: `SELECT ig_username, scopes FROM instagram_connections;`

### Conta privada: detectada pela consequência, não por um campo

A doc é explícita: *"The Instagram professional account that owns the media objects
must be public to receive notifications for comments or @mentions."*

Mas **não existe campo de privacidade** neste caminho: `GET /me` no
`graph.instagram.com` expõe só `id, user_id, username, name, account_type,
profile_picture_url, followers_count, follows_count, media_count`. Não há
`is_private`; `is_published` existe só para contas Page-backed, no outro host.

Então a validação acontece onde o problema aparece: se a conta é privada, a
inscrição do passo 3 não se sustenta, `webhook_subscrito` fica `false` e a tela
avisa nomeando as duas causas prováveis (perfil privado ou "Permitir acesso às
mensagens" desligado). É menos elegante que barrar no OAuth — e é o que a API
permite fazer sem chutar.

Nota de escopo: contas **Business** não conseguem ficar privadas no Instagram; o
cenário atinge sobretudo contas **Criador de Conteúdo**.

### ⚠️ O webhook `comments` NÃO dispara antes do App Review

Este é o ponto que muda o cronograma. A doc de webhooks do Instagram diz, com
todas as letras:

- *"Advanced Access is required to receive `comments` and `live_comments` webhook
  notifications"*
- *"Your app must be set to Live in the App Dashboard for Meta to send webhook
  notifications"*

Ou seja: **comentar de verdade no post não chama o nosso webhook enquanto o review
não sair.** A premissa do §10 do spec ("os passos 1–4 podem ser testados
inteiramente em Standard Access") vale para tudo, *menos* para a entrega da
notificação. Confirme no painel antes de fechar o cronograma — mas planeje para o
caso pior.

**Como testar mesmo assim, sem esperar o review:** existe
`scripts/simular_comentario_instagram.py`, que monta o payload no formato real,
assina com o `INSTAGRAM_APP_SECRET` (a mesma assinatura da Meta) e faz o POST no
webhook. Dali em diante **tudo é real**: matching, dedupe, janela, throttle e o
envio do direct pela API. O único passo simulado é a entrega da notificação.

```bash
export INSTAGRAM_APP_SECRET=<o mesmo do backend>
python scripts/simular_comentario_instagram.py \
  --url https://api.hml.marketdash.com.br/webhooks/instagram \
  --ig-user-id <ig_user_id da conta conectada> \
  --media-id <id do post> --texto "quero"
```

Isso também resolve o screencast do App Review pela metade: o direct que chega no
vídeo é real. Se a Meta exigir o comentário real na gravação, aí é
ovo-e-galinha — vale abrir suporte antes de submeter.

### App Review

Solicitar **Advanced Access** para:

- `instagram_business_basic`
- `instagram_business_manage_comments`
- `instagram_business_manage_messages`

Nomes antigos (`business_basic`, etc.) estão descontinuados — o código usa os novos.
`instagram_business_content_publish` **não** é pedido: não publicamos nada.

**Antes de submeter**, conferir no painel se além das três permissões existe algum
tier/feature separado para Instagram Messaging (lição da saga do Marketing API
Access Tier: permissão aprovada ≠ recurso liberado). Se existir, vai na mesma
submissão. Business Verification já está ✓ e vale para o app inteiro.

O screencast precisa mostrar o fluxo ponta a ponta com conta real: conectar →
criar automação → um terceiro comenta → o direct chega.

**Os passos 1–4 da ordem de implementação são testáveis em Standard Access, com a
conta do Luiz, antes de o review sair.**

---

## 3. Variáveis de ambiente

```env
# Instagram — Business Login (NÃO são o App ID/Secret do Facebook)
INSTAGRAM_APP_ID=xxxxxxxxxxxxxxxx
INSTAGRAM_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
INSTAGRAM_OAUTH_REDIRECT_URI=https://hml.marketdash.com.br/dashboard/automacoes/callback
INSTAGRAM_WEBHOOK_VERIFY_TOKEN=<openssl rand -hex 32>
INSTAGRAM_API_VERSION=v23.0

# Travas de envio (padrões já aplicados; só mexer com motivo)
INSTAGRAM_MAX_PRIVATE_REPLIES_HORA=600   # teto da Meta é 750
INSTAGRAM_MAX_ENVIOS_SEGUNDO=5

# Já existentes, reaproveitados
SHOPEE_ENCRYPTION_KEY=<chave Fernet — criptografa o token do Instagram também>
CRON_SECRET=<já existente>
```

---

## 4. Migrations

| # | Arquivo | O que faz |
|---|---|---|
| 051 | `051_create_campaign_platform_insights.sql` | Breakdown Instagram×Facebook em Campanhas (Marketing API — **outra feature**, ver nota abaixo) |
| 052 | `052_instagram_automacao_comentario_direct.sql` | `instagram_connections`, `instagram_automations`, `instagram_events` |
| 053 | `053_cron_instagram_token_refresh.sql` | pg_cron diário 05h15 UTC → renovação de tokens |
| 054 | `054_instagram_webhook_subscription_state.sql` | Estado da inscrição de webhook por conta (já vem na 052; a 054 é só para quem aplicou a 052 antes desta correção) |
| 055 | `055_instagram_account_type.sql` | `account_type` na conexão (idem: já vem na 052, a 055 é o catch-up) |

Todas idempotentes, todas com RLS por `app.current_user_id`.

> **A 051 não é desta feature e não colide com o gasto por dia.** Ela cria uma
> tabela NOVA (`campaign_platform_daily_insights`) e só ACRESCENTA métodos ao
> `campaign_repository`. Conferido no diff: `rebuild_ad_spend_from_meta` — a
> função da rodada 5 / frente C, que decide o que o Meta sobrescreve em `AdSpend`
> — **não foi tocada**, nem a tabela `ad_spends`, nem a lógica de `covered_dates`.
> As duas únicas linhas removidas no repositório e no service do Facebook são
> linhas de `import`. O sync grava o breakdown por placement numa etapa separada,
> depois do loop de campanhas e **antes** do rebuild do AdSpend, sem alterá-lo.
>
> Ainda assim: se a correção do gasto por dia estiver em voo, **suba a 051 depois
> dela**, não junto. Não há dependência técnica — é só para não misturar duas
> mudanças na mesma janela de observação. Se preferir, a 051 e o card em Campanhas
> podem ficar num PR próprio, separados da automação.

---

## 5. Regras da Meta que moldam o código

| Regra | Onde está tratado |
|---|---|
| **1 private reply por comentário, para sempre** | `comment_id` é UNIQUE em `instagram_events`; erro subcode **2534014** é classificado como permanente e nunca retentado |
| **Janela de 7 dias** | Contada do `timestamp` do COMENTÁRIO (não de quando o webhook chegou) — `dentro_da_janela()`. Fora da janela: status `expirado`, zero chamadas |
| **750 private replies/hora** | Teto próprio de 600/h (`_checar_teto_horario`) + espaçamento de 5/s. Estourando, a task é reenfileirada com jitter — nunca descartada |
| **Entrega em Solicitações se não segue** | Avisado no Card 4 do editor e no preview do direct |
| **Conta precisa ser Profissional** | `account_type` conferido no OAuth; conta pessoal recebe erro `CONTA_NAO_PROFISSIONAL` com o caminho da correção |
| **Disclosure de automação** | Sugestão (não obrigação) no Card 4 |
| **Permitir acesso a mensagens** | Checklist com 3 passos antes do botão de conectar |

### A consequência da mensagem única

O link vai já na primeira (e única) private reply. **Não existe segunda mensagem
possível** para aquele comentário, a não ser que a pessoa responda
espontaneamente. Isso é escolha de produto: não tente contornar com retry, segunda
chamada ou mensagem extra — a API rejeita e conta contra a reputação do app.

---

## 6. O que foi implementado

### Backend

```
migrations/052, 053
app/models/instagram_automation.py            InstagramConnection, InstagramAutomation, InstagramEvent
app/schemas/instagram_automation.py
app/repositories/instagram_automation_repository.py
app/services/instagram_login_client.py        graph.instagram.com + classificação permanente×transitório
app/services/instagram_connection_service.py  OAuth, renovação, deauthorize, data deletion
app/services/instagram_automation_service.py  CRUD + grade de posts com cache de 15 min
app/services/instagram_comment_pipeline.py    matching, dedupe, janela, throttle, envio
app/utils/text_normalize.py                   normalização compartilhada com o matching de Sub ID
app/tasks/instagram_tasks.py                  fila + política de retry
app/api/webhooks/instagram.py                 handshake, assinatura, deauthorize, data deletion
app/api/v1/routes/instagram.py                conexão + automações (gate MAX)
app/core/plans.py                             menu "automacoes" só no MAX + MAX_ONLY_MENUS
```

### Endpoints

| Método | Rota | Gate |
|---|---|---|
| GET | `/webhooks/instagram` | handshake (público, valida verify_token) |
| POST | `/webhooks/instagram` | público, valida `X-Hub-Signature-256` |
| POST | `/webhooks/instagram/deauthorize` | público, valida `signed_request` |
| POST | `/webhooks/instagram/data-deletion` | público, valida `signed_request` |
| GET | `/api/v1/instagram/auth-url` | assinatura ativa |
| POST | `/api/v1/instagram/oauth/callback` | assinatura ativa |
| GET/DELETE | `/api/v1/instagram/connection` | autenticado |
| POST | `/api/v1/instagram/connection/subscribe` | assinatura ativa |
| GET | `/api/v1/instagram/media` | **MAX** |
| GET/POST | `/api/v1/instagram/automations` | **MAX** |
| GET/PUT/DELETE | `/api/v1/instagram/automations/{id}` | **MAX** |
| PATCH | `/api/v1/instagram/automations/{id}/status` | **MAX** |
| POST | `/api/v1/instagram/automations/{id}/duplicate` | **MAX** |
| POST | `/api/v1/internal/cron/instagram-token-refresh` | cron secret |

A rota de **conexão** fica fora do gate de propósito: se a assinatura cair de MAX
para Pro, a aluna precisa continuar conseguindo ver e remover a conexão que criou.

### Frontend

```
src/shared/types/instagram.ts
src/services/instagram.service.ts
src/stores/instagramConnectionStore.ts
src/features/dashboard/components/InstagramConnectionSettings.tsx   aba "Integração Instagram"
src/features/dashboard/components/SelecionarPublicacao.tsx          grade de posts
src/features/dashboard/components/AutomacaoPreview.tsx              mockup de celular
src/features/dashboard/components/InserirLinkModal.tsx              Meus Links + link manual
src/features/dashboard/pages/Automacoes.tsx                         /dashboard/automacoes
src/features/dashboard/pages/AutomacaoEditor.tsx                    /nova e /:id
src/features/dashboard/pages/AutomacoesCallback.tsx                 /callback
src/features/landing/pages/ExclusaoDeDados.tsx                      /exclusao-de-dados (público)
```

A conexão fica numa **aba própria em Configurações**, `Integração Instagram`, ao
lado de `Integração Facebook` — mesmo componente `Tabs` das demais, mesma
formatação (`<Instagram/> Integração Instagram`). Não é um card solto na página.

Menu **Automação Instagram** entre "Meus Links" e "Indique & Ganhe", badge Novo,
cadeado + modal de upgrade para Essencial e Pro.

Página pública `/exclusao-de-dados` — destino do callback de exclusão de dados
exigido pela Meta (o callback devolve `{url, confirmation_code}` e a Meta exige
que essa URL mostre o status do pedido em linguagem legível).

---

## 7. Pendências conhecidas

1. **Prints do checklist de pré-requisitos.** O spec §4.1 pede 3 prints numerados
   das telas do Instagram. A estrutura está pronta: soltar
   `passo-1-conta-profissional.png`, `passo-2-acesso-mensagens.png` e
   `passo-3-conectar.png` em `marketdash-frontend/public/instagram/` e eles
   aparecem sozinhos. Sem os arquivos, a tela mostra só o texto numerado (não
   quebra).
2. **App Review** ainda não submetido — obrigatório antes de liberar para alunas.
3. **Amarração de "próxima publicação"** é preguiçosa: acontece no primeiro
   comentário de um post publicado DEPOIS da criação da automação (não existe
   webhook de "publiquei"). Se ninguém comentar, a automação segue esperando.
4. ~~Decidir um app × dois apps~~ — **decidido**: opção A, um app só, webhook
   apontando para HML e trocando na promoção.
5. ~~Confirmar o App ID~~ — **resolvido**: `1041324905491556` (app `MarketDash-IG`),
   já gravado no `.env` local. Falta replicar nas variáveis do Coolify de HML.

---

## 8. Passo-a-passo até a validação em HOMOLOGAÇÃO

### 8.1 Preparação na Meta

- [ ] Adicionar o caso de uso Instagram → API setup with Instagram login
- [ ] Copiar Instagram app ID e secret
- [ ] Decidir **um app × dois apps** para HML/PROD (§2.1)
- [ ] Cadastrar as 3 URLs (redirect, deauthorize, data deletion) apontando para HML
- [ ] Cadastrar o webhook e assinar o campo `comments`
- [ ] **Adicionar a conta profissional do teste** em Instagram → API setup with
      Instagram login → Generate access tokens → Add account (a conta precisa ser
      **pública**). Pular isso faz o OAuth falhar parecendo bug de código
- [ ] Conta de teste: Instagram Profissional do Luiz, com "Permitir acesso a
      mensagens" LIGADO, e o usuário como admin/testador do app
- [ ] Um segundo perfil (qualquer um) para comentar — a própria conta é ignorada

### 8.2 Deploy

- [ ] Aplicar migrations **051 → 052 → 053** no Supabase de homologação
- [ ] Definir as `INSTAGRAM_*` no Coolify (backend HML)
- [ ] `git push origin develop` no backend
- [ ] `git push origin develop` no frontend (o workflow roda lint + tsc + build)
- [ ] Conferir `/docs` listando as rotas `/api/v1/instagram/*`

### 8.3 Roteiro de validação

**Conexão**

1. [ ] Configurações → Instagram: o checklist de 3 passos aparece antes do botão
2. [ ] Conectar → **abre POPUP** (não redireciona a aba), a Meta **pede login
       mesmo se você já estiver logada** (`force_reauth`), pede os 3 escopos →
       autorizar → o popup fecha sozinho e a aba de origem atualiza com @usuario e
       "Conectado desde DD/MM". Se o popup estiver bloqueado pelo navegador, o
       fluxo cai em redirect na mesma aba e volta para Configurações — é o
       fallback previsto, não um bug.
3. [ ] `SELECT ig_username, status, token_expires_at FROM instagram_connections;`
       — status `ativo` e validade ≈ 60 dias à frente
4. [ ] Tentar conectar uma conta **pessoal** → erro claro sobre conta Profissional,
       sem stack trace e sem linha criada no banco *(§9 item 11)*

**Webhook**

4b. [ ] **A conta ficou inscrita?** Depois de conectar, a tela NÃO pode mostrar o
        alerta âmbar "ainda não estamos recebendo os comentários". No banco:
        `SELECT ig_username, webhook_subscrito, webhook_erro FROM instagram_connections;`
        → `webhook_subscrito = true`. Se vier `false`, nada vai disparar: leia o
        `webhook_erro`, corrija (perfil privado? "Permitir acesso às mensagens"
        desligado?) e use o botão **Tentar de novo**.
        Conferência direta na Meta, se quiser:
        `curl "https://graph.instagram.com/v23.0/<IG_ID>/subscribed_apps?access_token=<token>"`
        (este GET não é documentado neste caminho — se a Meta recusar, não é sinal
        de problema; vale o `webhook_subscrito` do banco).

5. [ ] No painel da Meta, botão **Test** no campo `comments` → deve responder 200
6. [ ] `GET https://api.hml.marketdash.com.br/webhooks/instagram?hub.mode=subscribe&hub.challenge=abc&hub.verify_token=<token>`
       → responde `abc` em texto puro; com token errado → 403
7. [ ] POST sem assinatura válida → **403**, e nada gravado

**Automação — caminho feliz**

8. [ ] Criar automação: post específico, palavra `QUERO`, resposta pública com 3
       variações, texto de DM com um link de Meus Links
9. [ ] Publicar → aparece na lista como Ativa
10. [ ] Do segundo perfil, comentar `quero` no post.
        **Se o app ainda não tem Advanced Access, isso não vai disparar nada** — use
        `scripts/simular_comentario_instagram.py` no lugar (ver §2). O restante do
        roteiro é idêntico: o direct que chega é real.
11. [ ] O direct chega (Caixa de Entrada se segue; **Solicitações** se não segue)
12. [ ] A resposta pública aparece embaixo do comentário
13. [ ] `SELECT comment_id, dm_status, reply_status FROM instagram_events ORDER BY id DESC LIMIT 5;`
        → `enviado` / `enviado`

**Testes de aceite do §9**

14. [ ] **Matching** — comentar, um por vez: `quero`, `QUERO`, `Eu quero esse!!`,
        `Quéro`, `queroo` → todos disparam. `queria` → não dispara *(cobertos por
        `tests/unit/test_instagram_matching.py`, confirmar em produção real)*
15. [ ] **Dedupe** — a mesma pessoa comenta `quero` de novo no mesmo post → **não**
        recebe segundo direct; `instagram_events` registra `duplicado` *(item 2)*
16. [ ] Mesma pessoa em **outro** post com automação ativa → recebe direct *(item 3)*
17. [ ] A **própria aluna** comenta `quero` no post dela → nada acontece *(item 4)*
18. [ ] **Janela** — `python scripts/simular_comentario_instagram.py ... --idade-dias 8`
        → status `expirado` em `instagram_events`, nenhuma chamada de envio *(item 5)*
19. [ ] **Volume** — 100 comentários em 2 minutos (laço sobre o script de
        simulação, variando `--commenter-id`) → 100 directs, 0 duplicados, 0 erros
        de rate limit. Conferir que o log NÃO traz `Instagram: teto horário` e que
        `SELECT count(*) FROM instagram_events WHERE dm_status='enviado';` = 100 *(item 6)*
20. [ ] **Rotação** — 6 comentários com 3 variações → cada variação exatamente 2
        vezes, em ordem *(item 7)*
21. [ ] **Ordem** — forçar falha na DM e conferir que a resposta pública **não**
        sai *(item 8)*.
        Responder o comentário manualmente pelo app **não serve**: isso é resposta
        pública, não consome a private reply, e a DM sairia normal — o teste
        passaria sem testar nada. Duas formas que funcionam de verdade:
        - **(a) mensagem inválida**, a mais cirúrgica:
          `UPDATE instagram_automations SET dm_texto = '' WHERE id = <id>;`
          (o SQL passa por cima da validação da API, que exige texto). A Meta
          recusa a mensagem vazia → erro permanente → resposta pública pulada.
          Devolver o texto depois: `UPDATE ... SET dm_texto = '<texto>'`.
        - **(b) versão de API inexistente**: subir o backend com
          `INSTAGRAM_API_VERSION=v99.0`. Toda chamada falha. Efeito mais amplo —
          serve se você quiser ver o caminho de erro inteiro.
        Depois: `SELECT dm_status, reply_status FROM instagram_events WHERE comment_id='...';`
        → `falhou` / `pulado`. A lógica em si já tem cobertura determinística em
        `test_instagram_pipeline.py::test_8_se_a_dm_falha_a_resposta_publica_nao_e_enviada`.

**Conexão — casos de borda**

22. [ ] Remover o app em Instagram → Configurações → Apps e sites → o deauthorize
        chega, as automações viram `pausada` e o card mostra o alerta *(item 9)*
23. [ ] `UPDATE instagram_connections SET token_expires_at = now() + interval '9 days';`
        → `SELECT public.trigger_instagram_token_refresh();` → validade avança ~60
        dias e `last_refreshed_at` é preenchido *(item 10)*
24. [ ] `SELECT * FROM net._http_response ORDER BY created DESC LIMIT 3;` → 202.
        Se vier 401, o secret do Vault não bate com o `CRON_SECRET`

**Plano**

25. [ ] Conta **Essencial** acessando `/dashboard/automacoes` direto pela URL →
        bloqueada. E `GET /api/v1/instagram/automations` com o token dela → **403
        PLANO_INSUFICIENTE** *(item 12 — o bloqueio precisa ser do backend)*
26. [ ] Conta Pro → mesmo comportamento (o menu é exclusivo do MAX)
27. [ ] Conta MAX → acesso completo

**Regressão**

28. [ ] Meta Ads: conectar/desconectar e a tela Campanhas seguem iguais
29. [ ] Shopee: sync e comissões inalterados

### 8.4 Go / no-go

**Go** exige: caminho feliz funcionando ponta a ponta; itens 14–21 conferidos; 100
comentários sem duplicata nem 429; deauthorize e renovação de token funcionando;
gate de plano barrando no backend; zero regressão em Meta Ads e Shopee.

**No-go**: qualquer direct duplicado; resposta pública saindo sem a DM ter saído;
429 da Meta com uma única conta de teste; comentário da própria conta disparando
automação (indica loop).

---

## 9. Promoção para PRODUÇÃO

1. [ ] **App Review aprovado** para as três permissões (sem isso, só admins e
       testadores do app conseguem conectar)
2. [ ] Cadastrar as URLs de produção (redirect, deauthorize, data deletion, webhook)
3. [ ] Aplicar migrations 051 → 052 → 053 no Supabase de produção
4. [ ] Definir as `INSTAGRAM_*` no Coolify de produção
5. [ ] Merge `develop` → `main`: **backend primeiro** (o frontend chama rotas que
       precisam existir), frontend depois
6. [ ] Repetir os itens 1–13 e 22–27 em produção, com a sua conta
7. [ ] Acompanhar 24h:
       `SELECT dm_status, count(*) FROM instagram_events WHERE processed_at > now() - interval '24 hours' GROUP BY 1;`

**Rollback.** Nada é destrutivo. Para desligar sem redeploy:
`UPDATE instagram_automations SET status = 'pausada' WHERE status = 'ativa';`
Para parar a renovação: `SELECT cron.unschedule('instagram-token-refresh-diario');`
Para cortar na origem: remover a assinatura do campo `comments` no painel da Meta.

---

## 10. Ordem de implementação (spec §10) — situação

| # | Passo | Status |
|---|---|---|
| 1 | Conexão OAuth + tokens + renovação + deauthorization | ✅ |
| 2 | Webhook + assinatura + persistência de eventos | ✅ |
| 3 | Matching + dedupe validados contra o §9 | ✅ (68 testes) |
| 4 | Envio (private reply + resposta pública) + fila + throttle | ✅ |
| 5 | Telas | ✅ (faltam os 3 prints do checklist) |
| 6 | Submissão do App Review com screencast | ⬜ pendente |

> **Ressalva ao §10 do spec.** O spec afirma que os passos 1–4 são testáveis
> inteiramente em Standard Access. Isso vale para o código todo, mas **não para o
> gatilho**: a doc da Meta condiciona a entrega do webhook `comments` a Advanced
> Access + app em Live. Use `scripts/simular_comentario_instagram.py` para fechar
> o ciclo antes do review — ver §2.
