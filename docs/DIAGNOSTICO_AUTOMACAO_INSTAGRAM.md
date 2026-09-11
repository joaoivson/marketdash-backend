# Automação de Instagram não respondeu — por onde olhar

Ordem deliberada: cada passo elimina uma causa, e o primeiro é o que respondia
errado antes da migration 083. Em 11/09/2026 esta sequência teria custado 10
minutos em vez de um dia.

## 1. A Meta entregou o comentário?

```sql
select desfecho, count(*), max(recebido_em)
from instagram_webhook_entregas
where recebido_em > now() - interval '24 hours'
group by 1 order by 2 desc;
```

| o que se vê | o que significa | o que fazer |
|---|---|---|
| **nenhuma linha** | a Meta não entregou nada | vá para o passo 2 |
| `assinatura_invalida` | entregou e recusamos | `INSTAGRAM_APP_SECRET` errado ou rotacionado |
| parada em `enfileirado` | fila sem consumidor | worker Celery morto — ver `celery-filas` |
| `ignorado` | o pipeline descartou | o motivo está na coluna `detalhe` |
| `enviado` | funcionou | o problema é outro post — passo 4 |

Linha por comentário específico:

```sql
select recebido_em, tipo, media_id, desfecho, detalhe, processado_em
from instagram_webhook_entregas
order by recebido_em desc limit 30;
```

## 2. A conta ainda está inscrita, segundo a META?

**Não confie em `instagram_connections.webhook_subscrito`** — é o retrato do dia
da conexão, não a verdade viva. Pergunte à fonte:

```
GET https://graph.instagram.com/v25.0/{ig_user_id}/subscribed_apps?access_token=...
```

Esperado: `subscribed_fields: ["comments", "messages"]`. Se vier vazio ou sem
`comments`, a inscrição caiu — o botão "tentar de novo" da tela de Configurações
chama `assinar_webhook` e resolve.

⚠️ **Produção e homologação usam o MESMO app da Meta**, e um app tem UMA URL de
callback. Se alguém apontou o callback para hml, produção para inteira, em
silêncio. Ver `project_instagram_app_meta_compartilhado` na memória.

## 3. A nossa ponta está viva?

Injeta um webhook assinado, como se viesse da Meta. Daí em diante tudo é real:

```bash
export INSTAGRAM_APP_SECRET=<o do backend>
python scripts/simular_comentario_instagram.py \
  --url https://api.marketdash.com.br/webhooks/instagram \
  --ig-user-id <conta> --media-id <post> --texto "quero"
```

Marcador rápido, sem efeito nenhum: um POST **sem** assinatura devolve 403 e
grava uma linha `assinatura_invalida`. Se a linha aparece, o código está no ar.

## 4. O post tem automação? (a causa mais comum)

`escopo = post_especifico` cobre UM post. Uma conta que publica todo dia pedindo
"Comente X" precisa de uma automação por publicação — e comentário em post
antigo continua chegando por meses.

```sql
select a.nome, a.status, a.media_id, a.media_permalink,
       count(e.id) filter (where e.dm_status = 'enviado') as directs
from instagram_automations a
left join instagram_events e on e.automation_id = a.id
where a.user_id = :user_id
group by 1,2,3,4 order by a.id;
```

Comparar com as publicações reais:

```
GET https://graph.instagram.com/v25.0/{ig_user_id}/media
    ?fields=id,permalink,timestamp,comments_count,caption&limit=100
```

Em 11/09/2026, na conta @promosdabeatrizz_: **283 publicações, 278 pedindo
"Comente X", 9 com automação.** Não havia bug nenhum.

## 5. Os limites da Meta que o código respeita

- **1 private reply por comentário, para sempre** (subcode 2534014)
- **janela de 7 dias** contada do timestamp do COMENTÁRIO, não da entrega
- **24h** para reply de story (é mensageria, não comentário)
- **600 private replies/hora** e 5/s, com reenfileiramento

Esses aparecem em `instagram_events.erro_codigo`, não no ledger.
