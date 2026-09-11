# Automação Instagram "parada" — 11/09/2026 · RESOLVIDO (não era bug)

Conta: `lfernandooliveira@outlook.com` (user_id 9, @promosdabeatrizz_, PRODUÇÃO).
Queixa: comentários "Quero" sem direct há 2 dias.

**Veredito: a automação funciona. Ela cobre 9 de 278 posts que pedem comentário.**

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Mapear o fluxo no código | webhook → Celery → pipeline → DM | eu | ✅ | — | — |
| 2 | Estado da conexão em prod | conexão/token/inscrição | eu | — | ✅ | — |
| 3 | Estado das automações | 9 ativas, palavras, media_id | eu | — | ✅ | — |
| 4 | Eventos gravados em prod | 19, nenhum `sem_match` | eu | — | ✅ | — |
| 5 | Automação × reel do print | permalink bate com `Dc3rR4fRqBP` | eu | — | ✅ | — |
| 6 | Endpoint do webhook no ar | 403 esperado em prod | eu | — | ✅ | — |
| 7 | Ledger de entrega (migration 083) | tabela + gravação + carimbo na task | eu | ✅ | ⬜ | — |
| 8 | Envs de produção (Coolify) | prod e hml usam o MESMO app da Meta | eu | — | ✅ | — |
| 9 | Logs da API de produção | endpoint do Coolify trava (75s × 2) | eu | — | ❌ | — |
| 10 | Descartar mudança de código | nada de Instagram em `main` desde 02/09 | eu | ✅ | ✅ | — |
| 11 | Inscrição real na Meta | `subscribed_apps` → `[comments, messages]` | eu | — | ✅ | — |
| 12 | Comentários reais do reel do print | 3 de terceiros; os 2 pós-automação, respondidos em 8s | eu | — | ✅ | — |
| 13 | Cobertura na conta inteira | 283 posts · 278 pedem · **9 com automação** | eu | — | ✅ | — |
| 14 | Subir a 083 | cherry-pick para `main` + aplicar migration | João + eu | ⬜ | ⬜ | — |
| 15 | Decidir o produto | como cobrir 278 posts sem criar 278 automações | **João** | ⬜ | ⬜ | ⬜ |

Etapa 9 permanece ❌: o endpoint de logs do Coolify não responde (`code=000` em
75s, duas tentativas). Não bloqueou o diagnóstico — a Graph API respondeu tudo.

Etapa 11 foi feita com o token real de produção. A Meta confirma a conta inscrita
em `comments` **e** `messages`, conta `BUSINESS`. A inscrição está perfeita.

## A medição que fecha o caso

```
Publicações na conta ................................... 283
Que pedem "Comente X" na legenda ....................... 278
Que têm automação no MarketDash ........................   9
Pedem comentário, sem automação, e já receberam ........ 111
```

O reel do print (`Dc3rR4fRqBP`) **tem** automação e ela funcionou: os dois
comentários de terceiros posteriores à criação receberam DM + resposta pública
em **8 segundos** (11/09 12:27:50 → 12:28:00 e 20:02:23 → 20:02:31). O terceiro
comentário do post é de 09/09 07:07 — 8h **antes** de a automação existir.

Posts antigos continuam recebendo comentário por meses: o reel MAMADEIRA, de
29/06, recebeu comentário em 10/09. São esses que ficam sem resposta.

## O erro de diagnóstico que eu cometi

Conclui, pelo banco, que "a Meta não está entregando os webhooks" — porque os 19
eventos da produção tinham todos `dm_status = enviado` e nenhum `sem_match`.

O argumento estava certo, a conclusão errada. `sem_match` só é gravado quando
**existe automação cobrindo o post**. Nos 9 posts cobertos quase só chega a
palavra pedida, então 100% de match é o esperado. O tráfego real está nos 111
posts sem automação — e esse caminho retorna **sem gravar nada**.

É exatamente o buraco que a migration 083 fecha.
