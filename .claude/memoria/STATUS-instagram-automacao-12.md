# Automação 12 ("Calcinhas Algodão") sem resposta — 17/09/2026

Conta: `lfernandooliveira@outlook.com` (user_id 9, @promosdabeatrizz_, PRODUÇÃO).
Queixa: `/p/Dc3rR4fRqBP/` tem 50+ comentários; a tela mostra 4 capturados / 4 directs.
Pedido extra do João (17/09): opção de enviar RETROATIVO para quem comentou e não recebeu.

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Automação 12 no banco | post `18118006687931396`, criada 09/09 15:44, palavras ok; **pausada às 14:18 UTC de 17/09** (por alguém, durante a análise) | eu | — | ✅ | — |
| 2 | Ledger de entregas do post | só 4 entregas com esse media_id desde 11/09 (2 terceiros, 2 próprias) — **as 4 respondidas** | eu | — | ✅ | — |
| 3 | Padrão das mídias que perdem comentário | `18607394596042322` (70) e `18057555857538856` recebem **comentário E reply de story no mesmo id por 6 dias** → assinatura de ANÚNCIO | eu | — | ✅ | — |
| 4 | Payload da Meta p/ comentário em anúncio | referência: `media.ad_id`, `media.ad_title`, `media.original_media_id` — pipeline só lia `media.id` | eu | — | ✅ | — |
| 5 | Prova final: comentários reais do post × ledger | exige token da conta; SSH/segredos bloqueados para mim | **João** (script no container) | ✅ | ⬜ | — |
| 6 | Fix: casar por `original_media_id` + anúncio no ledger | pipeline + 5 testes; worker `42c0fdb` consumindo (comentário 14:49:44 processado em 0,4 s) | eu | ✅ | ⚠️ | — |
| 7 | Retroativo — backend | GET prévia real em produção (aut. 5 da conta de teste): 200, 1 de 20 | eu | ✅ | ✅ | — |
| 8 | Retroativo — frontend | produção, 1440+390: tela 1 = API 1, linhas 6/6/7, soma 20 = total | eu (mapa: `Explore: tela /dashboard/automacoes/:id`) | ✅ | — | ✅ |
| 9 | Script de diagnóstico | `scripts/diagnosticar_automacao_instagram.py` | eu | ✅ | ⬜ | — |
| 10 | Rótulo do contador | print do João em produção mostra o texto novo; conta de teste (gatilho qualquer) API 6/6 = tela 6/6 | eu | ✅ | ✅ | ✅ |
| 11 | Commit + push `develop` (2 repos) | autorizado pelo João 17/09 | eu | ✅ | — | — |
| 12 | Cherry-pick em `main` + testes/build no worktree | back 801 verdes (782+19); front tsc 26=26, build ok | eu | ✅ | — | — |
| 13 | Gate de produção | aprovado (back + 2× front) | **João** | — | ✅ | — |
| 14 | Conferir deploy | `/health` = `42c0fdb`, `version.json` = `6bdc1a3` | eu | — | ✅ | — |
| 15 | Validar em produção | prévia real (conta de teste, SEM enviar, escritas abortadas) + tela | eu | — | ✅ | ✅ |
| 17 | Remover "Cobrir publicações" | front `144bf4e` → main `6bdc1a3`; prod 1440+390: botão 0, Nova Automação 1 | eu | ✅ | — | ✅ |
| 18 | Números da automação 12 subirem | reconciliação de admin nas 9 automações: +14 no contador, 0 direct | eu | ✅ | ✅ | ✅ |
| 19 | Botão à vista (card + editor) | back `e11e4c4`, front `f152c04`; conta do Luiz, 1440+390: botão nos 9 cards e no editor | eu | ✅ | — | ✅ |
| 20 | Rotas de suporte (admin) | prévia + reconciliar, `require_admin`, guarda em teste | eu | ✅ | ✅ | — |
| 21 | Onde estão os 50+ comentários | 8 mídias do ledger (~200 coment.) fora das 298 orgânicas = anúncios; post 12 tem 15 | eu | — | ✅ | — |
| 22 | Ligar comentário de anúncio à automação | depende de a Meta mandar `original_media_id` (1º pós-deploy veio sem) | eu | ⬜ | ⬜ | ⬜ |
| 16 | Doc/CHANGELOG/DIARIO | escrito | eu | ✅ | — | — |

**Tela ⚠️ (etapa 8):** validada com Playwright em 1440 e 390, com as rotas de
retroativo **simuladas** (o endpoint não existe em produção). Resto da API real,
só GET. Tela 31 = API 31, linhas 4/2/6/7/3 iguais, soma 53 = total. Card de story
sem o item. Pausada trava o botão sem POST. Fecha ✅ depois do deploy, abrindo a
prévia real.

**API ⬜ (etapas 6, 7, 9):** só depois do deploy, com resposta real do backend.

**Bloqueio da etapa 5:** a prova de que os ~46 comentários faltantes chegaram com
o id do anúncio (e não "a Meta não entregou") só sai casando `GET /{media}/comments`
com `instagram_webhook_entregas.item_id`. Destrava com o script da etapa 9 rodado
dentro do container da API de produção, ou com a prévia do retroativo na tela.

**Pré-existente, fora do escopo:** `test_waha_servidores::test_cache_evita_uma_query_por_mensagem`
e `test_campaign_repository_unpaid_status::test_unpaid_nao_entra_no_resumo_de_sub_ids_do_modal_de_vinculo`
vermelhos em `develop` sem as mudanças desta rodada.

**API ⚠️ (etapa 6):** o fix do anúncio está no worker de produção e o worker
consome. Ainda não passou por ele nenhum comentário de anúncio de post com
automação. Fecha quando o ledger mostrar `detalhe` com `comentário em anúncio`.

**Etapa 18 (contadores da automação 12):** o deploy não muda número nenhum sozinho.
A automação está PAUSADA desde 14:18 UTC. Os comentários perdidos não viram
evento, a não ser pelo retroativo. Os de mais de 7 dias não viram nunca.

**Validação 17/09 tarde (conta real do Luiz, escrita bloqueada, "Enviar" nunca clicado):**
banco = API = tela nos 9 cards (ex.: 12 → 5/4, 7 → 6/0), botão em todos e no
editor. Prévia 12: API 15 = tela 15 (4 respondidos, 5 sem palavra, 1 analisado,
5 próprios, 0 elegíveis).
