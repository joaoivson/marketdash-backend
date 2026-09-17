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
| 6 | Fix: casar por `original_media_id` + anúncio no ledger | pipeline + 5 testes | eu | ✅ | ⬜ | — |
| 7 | Retroativo — backend | GET prévia / POST envio, priority 9, trava, 14 testes | eu | ✅ | ⬜ | — |
| 8 | Retroativo — frontend | item no ⋮ do card + `RetroativosModal` | eu (mapa: `Explore: tela /dashboard/automacoes/:id`) | ✅ | — | ⚠️ |
| 9 | Script de diagnóstico | `scripts/diagnosticar_automacao_instagram.py` | eu | ✅ | ⬜ | — |
| 10 | Rótulo do contador | "comentários com a palavra-chave" (era "capturados", lido como total do post) | eu | ✅ | — | ⬜ |
| 11 | Commit + push `develop` (2 repos) | autorizado pelo João 17/09 | eu | 🔄 | — | — |
| 12 | Cherry-pick em `main` + testes/build no worktree | sem migration, arquivos idênticos main×develop | eu | ⬜ | — | — |
| 13 | Gate de produção | aprovação no GitHub | **João** | — | ⬜ | — |
| 14 | Conferir deploy | `/health` e `version.json` == SHA | eu | — | ⬜ | — |
| 15 | Validar em produção | prévia real (conta de teste, SEM enviar) + tela | eu | — | ⬜ | ⬜ |
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
