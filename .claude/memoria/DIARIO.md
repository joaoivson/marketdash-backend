# Diário — MarketDash Backend

> **Append-only. Entrada nova no topo.** Nunca reescreva entrada antiga — se
> estava errada, escreva uma nova dizendo que estava errada e por quê.
>
> Formato: `## AAAA-MM-DD — título curto` · o que mudou · **por quê** · o que
> ficou pendente. O "por quê" é a parte que o `git log` não dá.
>
> Mudança visível ao usuário também entra no `CHANGELOG.md` da raiz. Aqui vai
> o raciocínio; lá, o fato.

---

## 2026-08-26 — O bug que só aparece quando você roda de verdade

`ShopeeIntegrationService(self.db)` — o construtor espera o **repository**, não
a Session. Passar a Session dá `AttributeError: 'Session' object has no
attribute 'get_by_user_id'`, e o erro caía **dentro de um `except Exception`**
nos dois lugares que usam a função:

- `roteiro_envio_service._resolver_short_links` (F3): toda linha de passo de
  oferta virava `pulado` com erro `"short_link"`. Ou seja, **nenhum envio de
  oferta jamais teria link em produção** — e a execução terminava "com sucesso",
  só com linhas puladas.
- `monitoramento_tasks._converter` (F8): toda replicação virava captura em erro.

**Por que a suíte não pegava:** os testes do motor injetam `short_link_factory`
e por isso nunca constroem o service. O caminho de produção não tinha teste
nenhum, e o `except` genérico transformava um erro de programação em "estado de
negócio plausível" — `pulado` é uma coisa que acontece de verdade, então nada
parecia errado.

Duas lições que valem mais que o conserto:

1. **`except Exception` largo em volta de uma construção de objeto esconde bug
   de código como se fosse falha externa.** O `except` ali existe por um motivo
   legítimo (a Shopee cai, e o lote não pode abortar), mas ele engolia junto uma
   classe de erro que nunca deveria acontecer.
2. **Injetar a dependência no teste tira do teste justamente o caminho que
   quebra.** O teste novo (`test_short_link_construcao.py`) constrói pelo mesmo
   caminho do código de produção e verifica os call-sites por leitura de fonte —
   feio, mas é o que teria pego.

Encontrado rodando a replicação de verdade contra hml, não lendo código.

---

## 2026-08-26 — Grupos F8: monitoramento, e por que a promessa de privacidade precisou ser reescrita

**O achado que mais assusta é o mais banal.** `extrair_link` normalizava a URL
(`https://` na frente) e era essa forma que ia para `link_original`. Só que
`texto_para_envio` fazia `replace(link_original, meu_link)` contra o texto
**cru**. Quando o dono do grupo cola sem esquema — que a própria regex existe
para aceitar — o `replace` não casa com nada e é um no-op. A conversão do link
tinha dado certo, então não caía em erro: a mensagem saía para TODOS os grupos
da afiliada com o link do CONCORRENTE, marcada como "replicada". A lição é
sobre representação: **guardar a forma normalizada e usá-la para casar contra o
original é sempre um bug esperando o dia certo**. Agora o link é guardado como
apareceu, e a normalização existe só para descobrir o marketplace.

**ReDoS de graça no caminho quente.** `(?:www\.|[a-z0-9-]+\.)+` — as duas
alternativas casam `www.`, então cada segmento ganhava dois caminhos e a falha
no fim explorava todos. Medido: `"www."×22` = 1,1s; ×30 seriam minutos. E o
texto vem de um grupo de TERCEIRO: qualquer membro do grupo monitorado podia
travar o threadpool inteiro do FastAPI sem ter conta aqui. Remover a alternativa
redundante deixou linear. Regex sobre entrada de terceiro merece um teste de
tempo, não só de resultado — ficou um.

**A moldura da privacidade estava mais estreita do que a realidade.** O evento
`message` do WAHA é por **sessão**, não por chat: com um monitoramento ligado, o
conteúdo de todas as conversas daquele número trafega até o nosso webhook, e é o
nosso primeiro `if` que descarta o que não é do grupo monitorado. Isso é
verdade operacional que a política precisava dizer — o texto anterior ("não
recebemos conteúdo de mensagem") sugeria um recorte por grupo que o gateway não
oferece. Mitigamos escolhendo UMA sessão ouvinte por monitoramento (antes,
todas as sessões no grupo passavam a escutar), mas a assimetria continua e está
declarada.

**Estado tem que ser restaurado, não invertido.** O rollback do toggle fazia
`m.ativo = not m.ativo`. Isso só acerta quando o PATCH mudou o campo — e havia
um caminho real (deletar com envio em andamento, depois salvar outro formulário)
em que o "desfazer" LIGAVA um monitoramento desligado, e o cron do dia seguinte
começava a capturar sem ninguém ter pedido. Guardar o valor anterior antes do
`setattr` custa uma linha.

**Claim atômico não basta sem o destravamento.** `capturada→replicando` resolveu
a corrida de dois workers, mas `task_acks_late` reentrega a task quando o worker
morre — e a reentrega não consegue reivindicar de novo. A captura ficava presa
para sempre: nem replicava, nem aparecia como erro. Todo claim precisa do par:
quem destrava o que ficou pelo caminho. Entrou no cron diário.

**`erro` não pode ser terminal quando a causa é passageira.** Shopee fora do ar
marcava a captura como erro; o repost da mesma oferta caía na deduplicação; a
oferta morria em definitivo. Agora replicar de novo reabre.

**Pendente:** a suíte do módulo depende do Postgres local (5434) e **pula em
verde** onde ele não existe; o teto de captura por mensagem com URL qualquer é
largo por padrão (mitigação é de produto: palavras-chave, retenção).

---

## 2026-08-26 — Grupos F7: os números do ciclo, e o bug que quase passou

**O achado da rodada: a tela de Resultados divergia do Dashboard em 294%.**
`_comissao_por_sub_id` somava `commission` de todas as linhas do período sem a
allowlist `STATUS_DO_KPI` — um `UNPAID` de R$500 entrava como comissão real — e
comparava status com `"canceled"` (um L) enquanto a Shopee manda `cancelled`
(dois). O docstring do módulo dizia, em letras garrafais, que a fórmula era a
do KpiService; o código não fazia isso. Comentário não é teste: a suíte passava
porque todo cenário usava status dentro da allowlist. O que pegou foi revisão
adversarial com dado sintético fora do caminho feliz.

**A lição de arquitetura:** `normalizar_sub_id` faz `rtrim('-')`. Ao mover o
filtro do sub_id para o SQL (era um `for` em Python sobre todas as linhas do
usuário), quase descartei em silêncio toda venda com `wg1-`. Regra de bolso:
função de normalização replicada em SQL precisa replicar TODOS os passos, e o
teste tem que usar um valor sujo — com valor limpo os dois caminhos concordam
e o bug fica invisível.

**Leads do Meta: preferimos errar para baixo.** Não deu para fechar se `lead`
é o agregado que já contém `offsite_conversion.fb_pixel_lead` (fonte pública
diz as duas coisas) e a conta real de hml não tem conversão de lead nenhuma
para decidir empiricamente. Ficou `max()`. O raciocínio é assimétrico e vale
registrar: lead inflado divide o CPL pela metade, faz anúncio ruim parecer bom
e a afiliada gasta MAIS; lead subestimado, no pior caso, a deixa cautelosa. As
duas direções não custam a mesma coisa, então o hedge não é neutro — é para o
lado que não queima dinheiro dela.

**Período não é decoração.** `eventos_por_grupo` contava desde sempre. Com o
filtro de 7 dias na tela, entradas históricas apareciam ao lado de comissão de
7 dias — e, pior, o rateio do gasto do período usava entradas de meses atrás:
o grupo que encheu em julho levava o gasto de agosto. A saída que anula um
"ficaram", essa sim, continua sem janela de propósito: quem entrou no período
e saiu depois não ficou.

**O teste que se autoenvenenava.** `test_roteiro_envio_fatia` começou a falhar
inteiro com "0 enviadas", com toda a cara de regressão do claim atômico. Era o
teto GLOBAL da plataforma (5.000 msgs/dia somando todas as usuárias): o banco
de teste é compartilhado e acumulava. O motor estava certo — parqueou, como
deve. Ficou um `autouse` que tira o teto do caminho e, de quebra, o teste que
o teto global nunca teve. Vale para qualquer limite global futuro: contador que
atravessa usuárias precisa de fixture, ou o teste vira bomba-relógio.

**Camadas.** A rota recalculava `gasto × (1 + imposto)` que o repository já
calculava. Duas fórmulas de dinheiro em camadas diferentes é uma que fica para
trás na próxima mudança de regra. `metricas()` passou a devolver
`gasto_com_imposto`, e `KpiService._taxas` virou `taxas` — um `_privado`
chamado por três módulos só engana quem lê.

**Pendente:** revalidar a semântica de `lead` quando uma campanha de grupos com
pixel rodar de verdade; a suíte da F7 depende do Postgres local (5434) e
**pula em verde** onde ele não existe — CI não protege o invariante que ela
existe para proteger.

---

## 2026-08-26 — Grupos F6: link de entrada, eventos e o hash de terceiro

**Página no backend, não no SPA.** O crawler do WhatsApp não executa JS: a
prévia customizada só existe com OG tags server-side, e o pixel tem que
disparar antes do redirect. O frontend passou a encaminhar `/g/*` por proxy
(nginx + vite) para o domínio continuar sendo o da spec — a revisão pegou que
sem isso TODO link cairia no NotFound.

**Diff de snapshot não substitui o evento.** Contagem líquida não diz quem
entrou nem quem saiu, e "entraram e ficaram" (o denominador do custo por
permanência) exige casar as duas pontas por identificador. Daí o
`group.v2.participants` — com o número virando `sha256(jid+salt)` no handler,
antes de qualquer persistência.

**Uma transação para escolher e registrar.** `FOR UPDATE SKIP LOCKED` no
vínculo campanha↔grupo: dois cliques simultâneos nunca recebem o mesmo último
lugar do grupo que está lotando.

---

## 2026-08-26 — Grupos F5: Ofertas (productOfferV2) e integracoes

**A limitação da API define o produto, não o contrário.** `productOfferV2`
exige `keyword` na prática (sem ela devolve vazio mesmo com categoria) e
ignora `sortType` quando a busca é por categoria. Insistir num "catálogo
navegável por categoria + ordenação" seria construir uma tela que a API não
sustenta. A tela virou busca por termo — e quando a afiliada escolhe só
categoria, mandamos o nome dela como keyword.

**Filtro honesto.** Comissão/preço/desconto são aplicados por nós sobre a
página retornada, porque a API não filtra por eles. A tela diz isso em uma
linha em vez de fingir que filtrou o catálogo inteiro.

**Migração de credencial em dois deploys.** `integracoes` nasceu com backfill
e escrita dupla; a leitura só vira no próximo ciclo, e `shopee_integrations`
não é dropada. O `credenciais_de` lê os dois formatos (JSON do backfill com
campo interno cifrado, e JSON cifrado inteiro) — é isso que permite os dois
conviverem sem migração big-bang.

**Achado de revisão que valia ouro:** o upsert é por (usuária, marketplace,
nome), então adicionar uma SEGUNDA conta sem nome sobrescrevia a primeira em
silêncio, com toast de sucesso.

---

## 2026-08-26 — Grupos F4: roteiros, templates e a IA no lugar certo

**`payload: list` sem genérico é uma armadilha do FastAPI.** A rota de salvar
passos virou `multipart/form-data` no OpenAPI e devolvia 422 com `input: null`
para qualquer JSON — o cliente não tinha o que fazer. Só apareceu quando a
tela tentou salvar. Agora tem teste de contrato lendo o OpenAPI das 3 rotas
que recebem lista: elas TÊM que declarar `application/json`.

**IA em tempo de envio seria o erro caro.** Custo por mensagem, latência no
meio do lote, risco de inventar preço. Gerar variações uma vez e sortear
resolve o mesmo problema (texto diferente por grupo = anti-ban) com custo
zero no disparo. E variação que perde `{link}` é descartada: sem o link do
grupo não há atribuição de comissão, que é o produto inteiro.

**O prompt que fala de placeholders não pode passar por `.format()`** — as
chaves que ele manda preservar são exatamente as que o format tenta expandir.
Quebrava 100% das gerações; o teste pegou antes de qualquer chamada real.

**Erro de ação não é erro de número.** A revisão mostrou que 403 num rename
(não sou admin daquele grupo) virava `auth` fatal e desconectava a sessão, e
que 5xx classificava o grupo como inválido — um passo sobre 50 grupos podia
desativar os 50. Regra que fica: só desative grupo com evidência de que ELE
sumiu; erro transitório e falta de permissão pulam a linha.

---

## 2026-08-26 — Grupos F3: o motor (claim atômico, fatias, janela, tetos)

Raciocínios que o diff não conta:

**Estado terminal é obrigação, não detalhe.** A revisão achou TRÊS formas de
uma execução ficar viva para sempre sem enviar nada: estagnada em `enviando`
(worker morto depois do flip — o tick agora resgata), 100% `pulada` com
`proxima_execucao_em` NULL (invisível ao tick — agora conclui na hora), e
janela sem nenhum dia ativo (livelock de 5 em 5 min — `proxima_abertura`
devolve None e o salvar da config recusa). Regra que fica: todo caminho de
saída do motor precisa terminar em estado terminal OU ter quem o reagende.

**Teto diário ≠ pausa.** Pausada exige clique da afiliada; teto diário reseta
sozinho. Roteiro de lançamento de vários dias morreria no primeiro teto —
agora parqueia para a abertura do dia seguinte.

**Datetime naive do cliente é BRT, não UTC.** `.astimezone()` em naive usa o
fuso do servidor (UTC no container): um agendamento para 15h sairia às 12h.

**Testes do motor precisam de Postgres real** (`SKIP LOCKED` não existe no
SQLite) e de **janela 24h no cenário** — rodando às 3h da manhã, o motor
recusava enviar e os 8 testes falhavam com razão. O teste que "falha porque o
código está certo" é o mais barato de diagnosticar errado.

---

## 2026-08-25 — Grupos F2: campanhas de grupos (059 antes dos models)

Entidade Campanha + vínculos com posição. O que o diff não conta: a 059 foi
aplicada em hml ANTES de escrever os models (lição da F1 — o --reload local é
deploy instantâneo); "arquivar libera vaga" do limite do plano vale nos DOIS
sentidos (desarquivar re-conta — pego em revisão, o teste consagrava só a
ida); PUT de grupos deduplica pela última ocorrência (payload repetido
estourava a PK composta no meio do salvar-ordem). Substituição de conjunto
(lista completa na ordem final) em vez de deltas: a tela reenvia tudo ao
arrastar, e delta driftaria.

---

## 2026-08-25 — Grupos F1: WAHA no lugar da Evolution, números e grupos das alunas

Migração TOTAL de gateway (decisão do plano de grupos): `waha_client.py` com o
mesmo desenho do EvolutionClient (`_pedir` + MockTransport, erro tipado), e o
resumo diário junto — `enviar_texto` agora fala `chatId` (`numero@c.us`).
Raciocínios que o diff não conta:

**Sessão de aluna assina SÓ `session.status`.** O webhook é um só para todas
as sessões, roteado pelo nome (`mkd{ref4}u{id}x{hex4}`); prefixo de outro
ambiente é ignorado — hml e prod podem dividir o mesmo servidor WAHA sem
fratricídio. Conteúdo de mensagem não chega ao backend (LGPD); o `message` só
existe na sessão do resumo, para o SAIR — e SAIR vindo de `@g.us` é ignorado
(desligaria o resumo de quem por acaso está num grupo com o número).

**O create_all mordeu antes da migration.** O app local roda com `--reload`
apontando para hml: salvar os models criou as 3 tabelas lá ANTES da 058. O
RLS-por-default do Supabase (deny-all sem policy) segurou; a 058 idempotente
por cima completou policies + índice parcial. Regra nova no DECISOES: em dev
local contra hml, migration ANTES do model.

**Validado contra WAHA real** (GOWS local, tag `arm` no Mac): criar sessão,
QR data-uri, idempotência (`422 already exists` → `ja_existia`), delete. RAM
do container com 1 sessão ≈ 420MiB (base) — o marginal por sessão se mede na
homologação com números reais antes do GA.

**Pendências da fase**: aplicar 058 em PRODUÇÃO antes do deploy (protocolo);
re-parear o número do resumo em cada ambiente (sessão não migra — decisão
consciente de não suportar importação); subir o serviço WAHA no Coolify.

---


## 2026-08-19 — Memória do time criada neste repo

Criada a estrutura `.claude/` (agents, commands, memoria, rules, skills,
settings) espelhando o padrão já em uso no monorepo vizinho.

**Por quê.** O contexto do projeto vinha vivendo em três lugares que não se
falam: `CLAUDE.md` (convenção), `CHANGELOG.md` da raiz (o que mudou) e a
memória pessoal do assistente (o porquê). O terceiro não é compartilhável e
não sobrevive à troca de máquina ou de pessoa — decisão cara como "priority 5
some em silêncio" ficava fora do repo.

`CONTEXTO.md`, `DECISOES.md` e este diário foram semeados por inspeção do
código, do `CHANGELOG.md` e do `git log` de `develop` — **não** por relato.
Onde a seção divergir do código, o código vence.

**Divergências encontradas ao semear** (viraram pendência em `DECISOES.md`):

- `CLAUDE.md` manda subir uvicorn na **8081**; o `docker-compose.yml` sobe na
  **8000** e o proxy do Vite aponta para **8000**. Quem segue o doc não
  conecta.
- Credenciais S3 literais versionadas no `docker-compose.yml`.
- Arquivos duplicados `* 2.py` em `services/`, `repositories/` e `models/`.

**Pendente:** ninguém validou este `CONTEXTO.md` contra o ambiente de
produção — ele descreve o repo, não o que está no ar.

---

## 2026-08-20 — Instagram: validação em hml e Rodada 1 de ajustes

**O webhook `comments` entregou com comentário REAL, sem Advanced Access.** O
Luiz comentou de outra conta no post da `@promosdabeatrizz_`: resposta pública
+ direct em menos de 10s, com o app ainda em Standard. A ressalva ao §10 do
spec saiu da doc. Falta saber se vale para conta fora do painel (hoje ela está
como testadora).

Conexão conferida: `ig_user_id` bate com o painel (17841471079591636),
`webhook_subscrito=true`, e os **três** escopos concedidos — inclusive
`instagram_business_manage_comments`. A tela de consentimento mostrar só duas
linhas era agrupamento da UI da Meta, não escopo faltando.

**Dois testes que o Luiz pediu:**

- *Botão na private reply:* texto puro e template com botão falham com o MESMO
  erro (code 100 / subcode 2534014) quando o `comment_id` é inválido — ou seja,
  o template **passou na validação de formato**. Não é prova definitiva: para
  isso é preciso queimar uma private reply real (a Meta permite UMA por
  comentário, para sempre).
- *Trava de `webhook_subscrito`:* setar `false` no banco **não** exercita a
  recusa. `_exigir_webhook_ativo` chama `garantir_webhook()` antes, a conta é
  re-inscrita na hora e a ativação passa (200). É o desenho ("tenta reparar
  antes de recusar"). Para ver a recusa, a re-inscrição precisa falhar de
  verdade — perfil privado ou "Permitir acesso às mensagens" desligado.

**Ajustes entregues:** escopo `proximo` removido (UI + backend), abas de
Configurações sem "Integração" (cabiam em 768px, sem scroll), caminho do passo 2
corrigido para o menu que existe hoje no iOS, "pública" no passo 1, grade de
posts virou fileira de 4 + modal com busca (224 posts carregados, `ESCOVA`
retorna 8), variações viraram um campo cada, busca no modal de links (por nome
e slug), e microcopy de uma frase por card.

**Achado nosso:** automação **já ativa** não ganhava o selo "Aguardando
conexão" quando o webhook caía — continuava dizendo "Ativa" sem disparar nada.
Corrigido; o selo agora vale para qualquer status.

**Achado colateral:** o subcode 2534014 é tratado como "já respondido"
(permanente), mas a Meta usa o MESMO subcode para comentário inexistente. O
comportamento (não retentar) está certo nos dois casos; a mensagem engana quem
for investigar.

## 2026-08-21 — Instagram Rodada 2: direct com botão

O direct passou a sair como template `button` da Meta (texto + web_url) em vez
de link colado no corpo. Campos novos `dm_link` e `dm_botao_texto` (migration
056, aplicada em hml).

Três caminhos em `montar_mensagem_dm()`: link+título vira template; só link volta
pro texto com o link no fim; nada vira texto puro. Os dois últimos são o
fallback que o Luiz pediu guardar — se a Meta recusar o template em produção, o
produto continua entregando.

Voltar não exige redeploy: `INSTAGRAM_DM_FORMATO=texto` no Coolify + restart.
A env var é lida ANTES do feature-flags.json de propósito (arquivo versionado
exigiria rebuild). Valor inválido cai no default, não desliga em silêncio.

UI: Card 4 em três campos, "Inserir link" passou a preencher o campo de link,
emoji no Card 3 e no Card 4, e o contador de caracteres alinhado com o seletor
(estava solto em outra linha).

**Estado da conexão mudou de dono:** estava em user 9 (Luiz), agora está em
user 1 (relacionamento@) — alguém desconectou e reconectou pela outra conta, e
o `disconnect` apaga conexão + automações junto. A automação atual é a
"Esfoliante" do user 1. Quem for testar precisa saber em qual conta está a
conexão.
