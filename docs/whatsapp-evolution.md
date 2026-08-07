# WhatsApp (Evolution API) — como colocar no ar

O que o código já faz e o que precisa ser criado à mão. Design em
`docs/superpowers/specs/2026-08-07-resumo-whatsapp-design.md`.

## 1. Subir a Evolution no Coolify

Um recurso novo por ambiente, no projeto App Backend
(`owocs8cgosw44sco0o0wg0o4`). Imagem `evoapicloud/evolution-api:v2.2.3`.

Variáveis mínimas:

```
AUTHENTICATION_API_KEY=<gere uma chave longa e aleatória>
DATABASE_ENABLED=true
DATABASE_PROVIDER=postgresql
DATABASE_CONNECTION_URI=<postgres da própria Evolution, NÃO o do MarketDash>
DATABASE_SAVE_DATA_INSTANCE=true
CACHE_REDIS_ENABLED=true
CACHE_REDIS_URI=<redis://…/6>
LOG_LEVEL=ERROR
```

> O banco da Evolution guarda a sessão do WhatsApp. Se ele for perdido, o
> número desconecta e precisa de QR de novo — não use um banco efêmero.

Depois de subir, crie a instância (uma vez, via API da própria Evolution):

```bash
curl -X POST "$EVOLUTION_URL/instance/create" \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d '{"instanceName":"marketdash","integration":"WHATSAPP-BAILEYS"}'
```

## 2. Variáveis na API do MarketDash

No recurso da API (hml: `r448swsggoock0wg80csws0k`):

```
EVOLUTION_URL=https://<host da evolution>
EVOLUTION_API_KEY=<a mesma AUTHENTICATION_API_KEY>
EVOLUTION_INSTANCIA=marketdash
EVOLUTION_WEBHOOK_TOKEN=<gere outra chave aleatória>
```

⚠️ Salvar variável no Coolify **não reinicia o container**. Depois de salvar:
`gh workflow run deploy-homologation.yml --ref develop`, e espere ~4 min.

Opcionais, com padrão razoável: `WHATSAPP_INTERVALO_MIN_S` (3),
`WHATSAPP_INTERVALO_MAX_S` (8), `WHATSAPP_TETO_DIARIO` (300),
`WHATSAPP_FALHAS_PARA_PARAR` (5).

## 3. Apontar o webhook do SAIR

Na Evolution, para a instância `marketdash`:

```bash
curl -X POST "$EVOLUTION_URL/webhook/set/marketdash" \
  -H "apikey: $EVOLUTION_API_KEY" -H "Content-Type: application/json" \
  -d '{"webhook":{
        "enabled": true,
        "url": "https://api.hml.marketdash.com.br/api/v1/whatsapp/webhook",
        "headers": {"X-Webhook-Token": "<EVOLUTION_WEBHOOK_TOKEN>"},
        "byEvents": false,
        "events": ["MESSAGES_UPSERT"]
      }}'
```

Sem isso, quem responder SAIR continua recebendo — e é assim que um número é
denunciado.

## 4. Conectar o número

Painel do MarketDash → Admin → Sincronizações → aba **WhatsApp**. A tela mostra
o QR e se renova sozinha a cada 20s. No celular do número dedicado: WhatsApp →
Aparelhos conectados → Conectar aparelho.

Conectado, o estado vira **Conectado** e o envio está liberado.

## 5. Agendar

Rode a migration `046_cron_resumo_whatsapp.sql` **só no banco do ambiente que
deve enviar**. Agendar nos dois com o mesmo número faz a afiliada receber duas
vezes — o índice único de `whatsapp_envios` não protege, porque são bancos
diferentes.

Pré-requisito: os secrets `cron_shopee_secret` e `backend_base_url` já existem
no Vault (vieram da migration 018).

## 6. Testar sem esperar as 9h

```bash
curl -X POST "https://api.hml.marketdash.com.br/api/v1/internal/cron/whatsapp-resumo?user_id=<id>" \
  -H "Authorization: Bearer $CRON_SECRET"
```

Manda só para aquela afiliada. Repetir no mesmo dia não duplica: o índice único
de `(user_id, tipo, dia)` recusa.

## Desenvolvimento local

```bash
docker compose --profile whatsapp up evolution
```

Sobe na porta 8085. Não faz parte do `up` padrão de propósito: conectar um
número aqui e outro no Coolify na mesma instância derruba a sessão dos dois.

## Quando o número for banido

Acontece. O que fazer:

1. A tela do admin mostra **Desconectado** e o lote para sozinho no disjuntor —
   nenhuma mensagem sai para ninguém, e ninguém é cobrado por isso.
2. Conecte outro número: crie a instância nova, troque `EVOLUTION_INSTANCIA`,
   redeploy, leia o QR.
3. Os opt-ins continuam válidos: quem confirmou volta a receber sem refazer nada.
