# Decisões, pendências e débitos — Backend

> Mudança visível ao usuário vai no `CHANGELOG.md` da raiz. Aqui fica o
> **porquê** — o que o changelog não carrega.
>
> Decisão revogada **não some** — vira linha nova dizendo o que revogou.

## Decisões

| Data | Decisão | Por quê |
|---|---|---|
| 2026-08-21 | **O direct sai como template `button` da Meta**, com o link em botão em vez de colado no texto | Link cru no meio da mensagem parece spam; botão parece mensagem de marca (é o que o ManyChat faz). O formato antigo continua no código como fallback em dois níveis — só link vira texto com link no fim; nada vira texto puro |
| 2026-08-21 | **Interruptor de formato é env var, não `feature-flags.json`** — `INSTAGRAM_DM_FORMATO` é lido ANTES do arquivo | O pedido era voltar em produção "sem redeploy grande". O JSON é versionado: mudar exigiria commit + rebuild de imagem. Env var no Coolify + restart resolve. Valor inválido cai no default, para digitação errada não virar mudança silenciosa de formato |
| 2026-08-21 | **Seletor de emoji com lista curta escolhida a dedo, sem biblioteca** | Uma lib de emoji custa centenas de KB no bundle de uma tela que a aluna abre pelo celular, e o conjunto usado numa mensagem de afiliado é pequeno e previsível |
| 2026-08-20 | **Escopo `proximo` ("próxima publicação") removido** da UI e do backend | A amarração era preguiçosa — acontecia no primeiro comentário de um post novo. Com dois posts publicados, grudava em qualquer um dos dois e a aluna só descobria depois; sem comentário, esperava indefinidamente. `qualquer` resolve o caso sem ambiguidade |
| 2026-08-20 | **Advanced Access NÃO é pré-requisito para homologar** — o webhook `comments` entrega com o app em Standard | Validado com comentário real em 20/08 (direct em <10s). Provável causa: a conta está como testadora no painel e o app está Live. Pendente: confirmar se vale para conta fora do painel |
| 2026-08-20 | **Trava de `webhook_subscrito` repara antes de recusar** — mexer no banco não a exercita | `_exigir_webhook_ativo` chama `garantir_webhook()`, que re-inscreve a conta na hora. Para testar a recusa é preciso que a re-inscrição falhe de verdade (perfil privado, "Permitir acesso às mensagens" desligado) |
| 2026-08-20 | **Métrica de assinante conta PESSOA, não `subscriber_key`.** O mesmo cliente tem mais de uma chave em dois casos: upgrade (duas `sub:`) e import histórico sem `subscription_id` + webhook com ele (`cpf:` + `sub:`) | No 2º caso o cancelamento **não alcança** a linha do import, que congela em "ativo" — uma cancelada seguiu somando R$49/mês no MRR. `_latest_by_subscriber()` consolida import × webhook; upgrade continua com duas chaves (são duas assinaturas de verdade) |
| 2026-08-20 | **Retrato histórico não pode usar evento que chegou depois do instante.** Base do churn = `assinaturas_pagas_em()` + atrasadas − canceladas no corte, tudo reconstruído das cobranças | `renewing_subscribers(as_of=...)` olhava o último evento de TODOS os tempos e só comparava `access_until >= as_of`: quem assinou em agosto entrava na base de julho e quem cancelou em agosto saía dela. Dava 6/41 onde o correto era 6/20 |
| 2026-08-20 | **`is_plan_change` da Kiwify não é confiável como sinal de upgrade** — o churn confere o estado real no corte (`ultimo_evento_ate()`) em vez de só respeitar o flag | Caso real 31/07: pagou 22:05, cancelou 22:07, MESMO plano dos dois lados, os 4 eventos marcados `is_plan_change=True`. Como `cancel_instants()` ignora troca de plano, a pessoa ficava viva no retrato para sempre. Mesmo defeito que já tinha derrubado `new_subscriptions()` (commit adff2dd) |
| 2026-08-20 | **Sincronização em homologação é privilégio de uma lista curta** (`app/core/sync_gate.py`, hoje só o Luiz Fernando). Vale nos 2 botões manuais e nos 2 caminhos de cron | hml bate na API real da Shopee/Meta com o rate limit de produção; várias contas de teste sincronizando gastam cota e poluem a validação. O gate liga **pela ref do banco** (`app/core/ambiente.py`), nunca por `ENVIRONMENT` — e responde `False` em produção e em dev local, porque um gate que ligasse em produção pararia o sync de todas as alunas |
| — | **Supabase só para auth e Storage.** Todo dado por SQLAlchemy | Um só lugar de verdade sobre schema e query; PostgREST no caminho duplicaria regra de acesso |
| — | **`get_current_user()` valida o token chamando `supabase.auth.get_user(token)`** — não decodifica JWT localmente | Revogação de sessão vale na hora; decodificar local aceitaria token revogado até expirar |
| — | **Toda query filtra por `user_id`**, com `SET LOCAL app.current_user_id` para RLS | Isolamento de dado de afiliado é o núcleo do produto; falha aqui é vazamento entre clientes |
| — | **`Profit = Commission − Ad Spend`**, não `Revenue − Cost` | O afiliado não recebe a receita da venda, recebe a comissão. `Revenue − Cost` daria lucro fantasma |
| — | **Camadas `routes → services → repositories → models`, sem pular** | Rota fina é testável e trocável; regra em rota vira regra duplicada no dia do segundo endpoint |
| 2026-07 | **Fila do Celery derivada do `DATABASE_URL`**, não de `ENVIRONMENT` | Produção e homologação dividem o MESMO Redis/0 e os dois workers consumiam a fila default. Task de produção caía no worker de hml, que não achava o registro no banco dele e **retornava em silêncio** — `status` ficava `pending` para sempre e `datasets` nunca registrou um único `status='error'`. Derivar de `ENVIRONMENT` não resolveria: os dois ambientes reportam "development" |
| 2026-07-28 | **Celery só usa priority 0 (interativo) ou 9 (batch)** — nunca o default 5 | O Redis não tem prioridade nativa; o Celery emula com uma fila por step `[0,3,6,9]`. Só as pontas são consumidas neste ambiente — o 5 caía num step intermediário e a task ficava enfileirada **para sempre**, aceita com 202 e nunca executada, sem erro. Foi a causa raiz do "sync manual não faz nada" |
| 2026-07-25 | **Sync da Shopee é upsert aditivo** — acabou o `DELETE` da janela | O delete apagava dado bom quando a janela vinha incompleta da API. Upsert nunca perde linha; `sync_runs` + `/admin/sincronizacoes` dão visibilidade do que rodou |
| 2026-07-25 | **Endpoint novo NÃO pode usar `user_id` como nome de query param** | `fetchWithAuth` do frontend injeta `?user_id=user_N` em **toda** request por compatibilidade. Um parâmetro nosso com esse nome recebe o valor errado, no formato errado, sem ninguém perceber |
| — | **Sync agendado é `pg_cron` + `pg_net` no Supabase chamando `/internal/cron/*`**, não Celery Beat | Beat exige um processo a mais no ar 24h; o cron do banco já é gerenciado e sobrevive a redeploy. Protegido por `CRON_SECRET` |
| 2026-07-20 | **Cron de sync roda 1×/dia, não de hora em hora** | A versão 24×/dia derrubou o banco compartilhado prod+hml. Resolvido de vez em 25/07, quando hml ganhou projeto Supabase próprio |
| — | **Cobrança Kiwify = um evento pago, chaveado por `order_ref`** (`app/services/charges.py`) | O array `Subscription.charges.completed` **não é fonte** — só serve para detectar webhook perdido (`unknown_array_charges`). Reintroduzi-lo como fonte volta a duplicar tudo que veio do import histórico |
| — | **Acesso se checa por `subscription_has_access()`, nunca por `is_active` cru** | Cancelamento na Kiwify mantém acesso até `access_until` — o cancelamento é o **último** evento que ela envia, então o acesso precisa cair sozinho por data |
| — | **O plano vem do `checkout_link` do webhook, não do `product_id`** | O `product_id` da Kiwify é o mesmo para todos os planos. Ler dele classifica todo mundo igual |
| — | **MRR ≠ acesso.** `renewing_subscribers()` é a base de MRR/ARPU/plano; `active_subscribers()` é a base da aba Uso, alertas e Clientes | São perguntas diferentes: "quem me paga mês que vem" ≠ "quem consegue entrar hoje" |
| 2026-08-13 | **Denominador do churn é `renewing_subscribers()`**, revoga o uso de `active_subscribers()` | Cancelado-com-acesso não pode churnar de novo; contá-lo no denominador diluía a taxa |
| 2026-08-13 | **Bruto do MRR usa preço de tabela (`list_price_cents`), não a última cobrança paga** | A última cobrança pode carregar cupom/desconto histórico e não representa o valor do plano. Líquido continua vindo do valor real |
| 2026-08-13 | **Bucketing por dia civil é feito em Python com `_brt_date()`, nunca com `cast(coluna, Date)` em SQL** | `cast` trunca no fuso da **sessão do Postgres**, não em BRT: uma janela de "7 dias" espalhava por até 8 datas distintas. Vale para 7d/30d/90d, "dias ativos" e qualquer contagem por dia |
| 2026-08-13 | **Toda população derivada de assinante tem que concordar em quem tem `user_id`** — e checar o campo **bruto** do evento (`ev.user_id`) | `_base_ativa()` já excluía importados do histórico sem conta criada; `list_clients()` não excluía, e card × lista divergiam (17 vs 26). Checar uma variável local resolvida por fallback de e-mail reabre o mesmo buraco |

## Pendências

| Prioridade | Item | Contexto | Status |
|---|---|---|---|
| **Crítica** | **Credenciais em texto plano em arquivo versionado do compose** — `docker-compose.yml` carrega `S3_ACCESS_KEY`/`S3_SECRET_KEY` literais e o `.claude/settings.local.json` da raiz tem senha de banco. Remover do arquivo **não apaga do histórico**: a correção é **rotacionar** | Achado em 19/08 ao montar esta memória | Pendente — rotação de credencial |
| **Alta** | **Não existe controle de versão de schema.** 75 arquivos em `migrations/`, sem tabela de aplicadas: "esta migration já rodou aqui?" só se responde inspecionando objeto a objeto, em 2 bancos | Mesmo problema já vivido no monorepo vizinho | Pendente com o João |
| Alta | **Rodada 7 do painel admin validada só contra homologação** — itens 1, 2, 3 e o achado card×lista precisam de reconfirmação contra produção | Ver seção de pendências no `CHANGELOG.md` da raiz | Pendente (precisa de acesso a produção) |
| Média | **Dedupe de login legado sem evidência de impacto** — `POST /auth/login` passou a usar `record_access()` (janela de 2min), mas o diagnóstico contra hml achou 0 pares <2min | Não confirma nem descarta o efeito em produção | Pendente — falta rodar contra produção |
| Média | **`list_clients()` de-dup por upgrade é frágil por natureza** — qualquer mudança em `is_plan_change` precisa reconferir os 4 achados da Rodada 6 (zero-rows, contaminação de total por CPF, candidatura efêmera) | Todos com teste de regressão sintético em `test_admin_metrics_service.py` | Vivo — checklist ao mexer |
| ~~Baixa~~ | ~~**Arquivos `* 2.py` duplicados**~~ | Deixou de ser cosmético: entraram no commit `352b0e9` e **quebraram o CI de homologação** — o espaço no nome fez o `xargs` do `py_compile` procurar `./app/models/campaign` | **Resolvido em 19/08** — apagados; CI agora usa `-print0 \| xargs -0` + step que falha nomeando o arquivo; `.gitignore` bloqueia `* [0-9].py` |
| Baixa | **`CLAUDE.md` diz porta 8081; compose e proxy do Vite usam 8000** | Quem segue o doc sobe na porta errada e o proxy do frontend não acha | Pendente — corrigir o doc |

## Débitos técnicos

| Item | Onde | Impacto | Plano |
|---|---|---|---|
| **Subcode 2534014 tem dois significados** | `instagram_login_client.py` | A Meta usa o mesmo subcode para "já respondido" e para "comentário inexistente". Tratamos como "já respondido" — o comportamento (permanente, não retentar) está certo nos dois casos, mas a mensagem engana quem investiga log | Diferenciar pela mensagem da Meta, ou tornar o texto genérico |
| **Sem Alembic** | `migrations/` | Migration é SQL solto aplicado à mão; ordem e idempotência são responsabilidade de quem roda | Conviver com disciplina, ou adotar Alembic com baseline |
| **`cost` / `profit` mortos em `dataset_rows_v2`** | modelo + tabela | As colunas existem e não são a fonte de nada — o KPI que a usuária vê é calculado **no frontend** a partir de `raw_data`. Confiar nelas dá número errado | Não usar; remover só com migração consciente |
| **CI deployava só a API** | pipeline | O worker Celery ficou semanas com código velho porque um `\|\| echo` mascarava a falha do deploy | **Resolvido em 03/08** — manter o olho quando mexer no CI |
