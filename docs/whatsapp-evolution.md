# WhatsApp (Evolution API) — como colocar no ar

Procedimento verificado em homologação em 07/08/2026. Design em
`docs/superpowers/specs/2026-08-07-resumo-whatsapp-design.md`.

**Homologação já está pronta** (serviço `n84cwos0g00cocwksg8wswkw`, em
`https://evolution.hml.marketdash.com.br`). Este documento serve para repetir
em produção e para consertar quando algo quebrar.

## 1. DNS antes de tudo

Um registro A apontando para o servidor do Coolify:

```
evolution.marketdash.com.br  →  31.97.22.173     (produção)
```

Sem DNS não há rota: o FQDN que o Coolify gera é `evo-<uuid>.marketdash.com.br`
e **não existe curinga** nesse domínio.

## 2. Criar o serviço

New Resource → busque **"Evolution Api"** no catálogo. O template já traz
Postgres e Redis. Não use imagem avulsa: `atendai/evolution-api` não existe
mais, e o catálogo já usa a certa (`evoapicloud/evolution-api`).

**Implante imediatamente, antes de mexer em qualquer coisa.** Ver as duas
armadilhas na seção 6.

## 3. Fixar as credenciais do Postgres

Environment Variables → Developer view. Troque as referências por **literais**:

```
SERVICE_USER_POSTGRES=<usuário atual>
SERVICE_PASSWORD_POSTGRES=<senha atual>
POSTGRES_USER=<o mesmo usuário>
POSTGRES_PASSWORD=<a mesma senha>
```

Sem isso o Coolify gera uma senha nova a cada deploy enquanto o volume mantém a
primeira — e a Evolution entra em crash-loop com `P1000: Authentication failed`.

## 4. Domínio

Pare o serviço (**Stop**): o campo de domínio fica bloqueado enquanto ele roda.
No sub-serviço `Api`, troque para:

```
https://evolution.marketdash.com.br:8080
```

O `:8080` é a porta do container, não a pública. Depois **Deploy**. O
Let's Encrypt emite o certificado sozinho — leva uns 2 minutos.

Confira: `curl https://evolution.marketdash.com.br/` deve responder
`{"status":200,"message":"Welcome to the Evolution API..."}`.

## 5. Variáveis na API do MarketDash

No recurso da API:

```
EVOLUTION_URL=https://evolution.marketdash.com.br
EVOLUTION_API_KEY=<SERVICE_PASSWORD_AUTHENTICATIONAPIKEY do serviço>
EVOLUTION_INSTANCIA=marketdash
EVOLUTION_WEBHOOK_TOKEN=<gere uma chave aleatória>
```

⚠️ Salvar variável **não reinicia o container**. Depois de salvar:
`gh workflow run deploy-homologation.yml --ref develop` (ou o workflow de
produção), e espere ~4 min.

Opcionais, com padrão razoável: `WHATSAPP_INTERVALO_MIN_S` (3),
`WHATSAPP_INTERVALO_MAX_S` (8), `WHATSAPP_TETO_DIARIO` (300),
`WHATSAPP_FALHAS_PARA_PARAR` (5).

**Não precisa criar a instância nem configurar o webhook à mão.** A tela do
admin faz as duas coisas na primeira vez que abre.

## 6. As duas armadilhas do Coolify

**Não marque "Connect To Predefined Network".** Isso põe os containers na rede
compartilhada, onde o alias `postgres` deixa de ser único — a Evolution conecta
no banco errado e entra em crash-loop com `P1000: Authentication failed`. O erro
mente: a credencial está certa (dá para provar entrando no container do Postgres
e autenticando por TCP). Desmarcar resolve.

**Credenciais regeneram a cada deploy** enquanto forem `${SERVICE_...}`. É o que
a seção 3 previne.

Diagnóstico só anda pelo **Terminal** do Coolify (menu lateral): escolha o
container, Connect, e rode `wget -qO- http://localhost:8080/` ou `psql`.

## 7. Conectar o número

Painel do MarketDash → Admin → Sincronizações → aba **WhatsApp**. A tela cria a
instância, aponta o webhook do SAIR e mostra o QR, que se renova a cada 20s.
No celular do número dedicado: WhatsApp → Aparelhos conectados → Conectar
aparelho.

Conectado, o estado vira **Conectado** e o envio está liberado.

## 8. Agendar

Rode `046_cron_resumo_whatsapp.sql` **só no banco do ambiente que deve enviar**.
Agendar nos dois com o mesmo número faz a afiliada receber duas vezes — o índice
único de `whatsapp_envios` não protege, porque são bancos diferentes.

Pré-requisito: os secrets `cron_shopee_secret` e `backend_base_url` no Vault
(vieram da migration 018).

## 9. Testar sem esperar as 9h

```bash
curl -X POST "https://api.hml.marketdash.com.br/api/v1/internal/cron/whatsapp-resumo?user_id=<id>" \
  -H "Authorization: Bearer $CRON_SECRET"
```

Manda só para aquela afiliada. Repetir no mesmo dia não duplica.

## Desenvolvimento local

```bash
docker compose --profile whatsapp up evolution
```

Sobe na porta 8085. Fora do `up` padrão de propósito: conectar um número aqui e
outro no Coolify na mesma instância derruba a sessão dos dois.

## Quando o número for banido

1. A tela do admin mostra **Desconectado** e o lote para sozinho no disjuntor —
   nenhuma mensagem sai, e ninguém é cobrado por isso.
2. Troque `EVOLUTION_INSTANCIA` para um nome novo, redeploy, e abra a tela do
   admin: ela cria a instância nova e mostra o QR.
3. Os opt-ins continuam válidos: quem confirmou volta a receber sem refazer nada.
