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

## 2026-09-05b — Medição de grupos: o que o documento supunha e o que os dados disseram

Terceiro documento delta do módulo, sobre a cadeia de medição: entrada, saída,
evasão, leads, CPL, custo por entrada e por permanência. Migration 081.

**O método mudou depois do erro de ontem.** Ontem eu declarei um bug fechado por
inferência e a medição do dia seguinte me desmentiu. Hoje comecei medindo, e
isso derrubou **três** diagnósticos do documento antes de qualquer linha de
código:

1. *"No #2 faltam 87 pessoas — leitura parcial ou paginação truncada."* Não é.
   É **defasagem**: a lista vem do último sync e o contador anda pelo webhook. A
   aritmética fecha exatamente — `lista + entradas − saídas após o sync =
   contador − 1`, e o −1 é o nosso próprio número, excluído da lista de
   propósito. Se eu tivesse "corrigido" a paginação, teria mexido em código
   correto e o número continuaria divergindo.
2. *"A captura funciona, a agregação não."* A agregação está certa: 7 dias do #2
   = 181 entradas / 68 saídas. O #1 mostra 1 entrada porque **está cheio** — a
   rotação manda todo mundo para o #2. O sintoma era o efeito pretendido.
3. *"`custom_link` gravado como URL absoluta."* `campanha_links` sempre guardou
   só o slug. O domínio errado era a `FRONTEND_URL`, corrigida de manhã.

O que sobrou de bug real era outra coisa em cada caso.

### O override de "cheio" — e uma decisão minha revogada em 24h

O relato do Luiz: dois grupos marcados "Cheio = Sim" à mão voltaram para "Não"
depois de um sync. Fui procurar no sync e não achei — porque o sync nunca tocou
em `cheio_override`.

**Quem apagava era a decisão que eu tomei ontem.** Eu tinha feito o override
"limpar sozinho quando a escolha bate com o automático", para o campo não ficar
pegajoso. O efeito colateral: marcar "Sim" num grupo **já cheio pela ocupação**
gravava `null` — nada era persistido, a assinatura não mudava, "Salvar ordem"
nem acendia, nenhum PUT saía. Depois o sync baixava a contagem e o grupo voltava
para "Não". Da tela, é indistinguível de "o sync apagou".

A correção não é desfazer a de ontem: o problema que ela resolvia (grupo que
esvazia e nunca volta à rotação) é real. A saída é o **terceiro estado
explícito** — Automático / Sim / Não. Agora nada limpa o override sozinho, e
"Automático" é a porta de volta. O Select passa a mostrar a **intenção**; o
resultado continua na coluna Ocupação, onde sempre esteve.

### "Vagas esgotadas" era CPC jogado fora

Com todos os grupos cheios, o clique caía numa página que não convertia — e o
anúncio continua rodando e cobrando. Agora o link manda para o primeiro grupo da
ordem, sem tela intermediária: sempre há gente saindo.

Duas escolhas do fallback que não são óbvias:

- Ele ignora `aberto`, `cheio` e o limite da campanha, mas **respeita a
  `capacidade` do WhatsApp** — acima dela o convite falha do lado deles, e
  mandar para lá seria trocar uma página inútil por um erro.
- Ele **não usa `FOR UPDATE SKIP LOCKED`**, que a rotação normal usa. Ali o lock
  distribui vagas; no fallback não há vaga a distribuir — todos vão para o mesmo
  destino. Com o lock, dois cliques simultâneos fariam o segundo cair em "vagas
  esgotadas" sem motivo, justamente o que o fallback existe para evitar.

E o clique **conta**, marcado `resultado='fallback_lotado'`. Sem marcar, o gasto
existiria no Meta e o clique não existiria aqui — a taxa de entrada melhoraria
artificialmente justo quando a operação está pior.

### Pausar não pausava nada

`status='pausada'` era um select sem efeito: `rotear()` só bloqueava `arquivada`
e o motor de roteiros nem lia o status. O documento pedia para **promover** esse
controle a toggle no cabeçalho — o que teria dado destaque a um interruptor que
não desliga. Fiz na ordem inversa: primeiro o efeito (link responde "campanha
pausada", roteiro para), depois o destaque.

### Evasão de 900%

Saídas ÷ entradas do período: 9 ÷ 1. A base virou `participantes + saídas` —
todo mundo que esteve dentro em algum momento da janela. É o único denominador
que garante saídas ≤ base, e portanto evasão ≤ 100%. Aplicado também na Visão
geral: bases diferentes fariam a mesma campanha mostrar duas evasões na mesma
sessão.

### "Leads" era clique

1.348 leads ao lado de 53 entradas, CPL de R$0,97 ao lado de R$24,64 de custo
por entrada, nada dizendo que não eram a mesma coisa. O pixel dispara no
carregamento do `/g/`, antes do redirect — é clique qualificado. Renomeado.

O critério de "sem medição" foi o que mais errei: comecei por "o grupo tem
`sub_id`?", e todo grupo tem — ele nasce na ativação. Só conta como medição
vínculo manual de Sub ID ou sub_id de grupo que **trouxe pedido de verdade**.

### Pendências

- **Migration 081 pendente em produção**, junto com 074–080. Ela é `ALTER TABLE`
  pura e sem bloqueio jurídico, mas sem ela `campanha_link_eventos.resultado`
  não existe e **todo** clique quebra, não só o fallback
- O telefone continua dependendo de **número conectado + sync** em hml — o Luiz
  precisa reconectar para a coluna sair preenchida
- Os Sub IDs antigos (`wgea`) convivem com os novos (`grupobeatriz2k7f`)
  permanentemente, por decisão

---

## 2026-09-05 — O telefone NÃO estava no payload; e o link de hml apontava para produção

Retifica a entrada de ontem no título e na conclusão. O que mudou hoje: janela de
30 dias na busca de Sub ID, `frontend_url` derivado do BANCO, e o telefone
resolvido pelo REST do WAHA em vez do webhook.

**A entrada de ontem estava errada onde importa.** Ela dizia "o telefone estava
no payload o tempo todo, no campo ao lado" — a leitura do webhook realmente
estava errada e a correção era necessária, mas ela **não** era suficiente, e eu
declarei vitória sem medir depois do deploy. A medida chegou hoje: dos eventos
gravados em homologação **depois** daquele deploy, **191 continuaram todos
`identificador_tipo='lid'`**. Zero telefone.

Ou seja: o evento `group.v2.participants` do WAHA **não manda `PhoneNumber`**. O
recon tinha marcado isso explicitamente como "NÃO ESTÁ PROVADO que o payload
carrega o telefone — a prova que existe é do payload REST de `/groups`, não do
webhook", e eu tratei a inferência como fato porque ela explicava o sintoma bem
demais. Explicar o sintoma não é o mesmo que ser a causa.

A leitura no webhook **fica**, com comentário dizendo por quê: ela cobre o dia em
que o WAHA passar a mandar o campo, e apagá-la por parecer inútil faria o
telefone ser descartado de novo. Quem resolve hoje é o payload REST, que
sabidamente traz `PhoneNumber` — é o que `_identidades` já lia para descobrir se
somos admin. O sync passou a preencher os eventos que só tinham o LID, casando
pelo `identificador_hash`, que é estável por construção; o hash não muda, só a
coluna exportável. É idempotente e depende de **número conectado**: sem sync não
há payload REST, e sem ele não há telefone.

**O link do grupo: quase repeti o erro que o repo já documenta.** O sintoma
chegou como "a página do grupo continua não funcionando", com 404 no celular. Não
era a rota: a tela de homologação mostrava `https://marketdash.com.br/g/8496c6c7`
— domínio de **produção**, onde o módulo não existe. `FRONTEND_URL` estava setada
como produção nos dois recursos de hml no Coolify, e o `.env` ainda tinha a
variável **duplicada**, com produção na última linha (o `python-dotenv` monta um
dict e a última vence).

A primeira correção que escrevi derivava a base de `ENVIRONMENT`. Só descobri o
problema ao medir a API depois do deploy: `/health` reporta
`"environment":"development"` em **homologação**. É a mesma armadilha que o
`CLAUDE.md` e o `celery_app` documentam há tempo — os dois ambientes reportam
`development` — e ela quase entrou de novo, agora com consequência pior: sem a
env explícita, **produção** iria para `localhost:8080`. Refeito com
`app/core/ambiente.identidade_do_banco`, a ref do projeto Supabase, que é a
fonte que o projeto já usa para isso.

Lição para a próxima: quando existir um helper de "que ambiente é este", usar
ele. A tentação de ler `ENVIRONMENT` é grande porque o nome promete exatamente
isso — e é justamente o que não cumpre aqui.

**O que sobrou de menor.** A busca de Sub ID agregava `dataset_rows_v2` sem
recorte de período a cada abertura do modal; o tempo crescia com a conta, então
quem vende mais esperava mais — justamente quem mais usa a tela. Janela de 30
dias, com o escopo dito na tela (sem isso a afiliada vê a comissão cair e acha
que perdeu venda). E um teste que eu mesmo escrevi ontem era flaky: comparava
LISTAS de um `query().all()` sem `ORDER BY`, e o Postgres não garante ordem.

Pendente: reconectar um número em hml e rodar o sync — é o que faltava para o
telefone existir de fato, na lista de participantes e nos eventos.

---

## 2026-09-04b — O telefone estava no payload o tempo todo, no campo ao lado

O que mudou: migration **080** (`campanha_grupos.cheio_override`,
`grupo_participantes`, `campanha_sub_ids`), correção do webhook de participantes,
fim do rateio de gasto por grupo, Leads/CPL na tela de Anúncios, soft-delete e
duplicação de campanha. Aplicada em **hml**; produção continua sem o módulo.

**O bug que a rodada anterior não fechou — e por quê.** A 079 criou
`grupo_eventos.identificador`, o service passou a preenchê-lo, o repository a
gravá-lo, e mesmo assim a exportação saiu com telefone vazio em 8 de 8 linhas. A
tentação era procurar o defeito na cadeia nova. Ele estava numa linha que ninguém
tocou, no webhook:

```python
campo(p, "id", "JID", "PhoneNumber", "LID")
```

`campo()` devolve o **primeiro** nome presente. E em grupo com endereçamento LID
o `JID` não é uma alternativa ao telefone — ele **é** o `…@lid`, com o telefone
num campo separado, ao lado. O próprio repo já documentava isso em
`whatsapp_grupo_sync_service._identidades`, escrito para resolver exatamente essa
confusão no sync. A informação existia; o webhook não a usava.

Medido antes de mexer: dos 49 eventos gravados depois do deploy da 079,
**49 eram `identificador_tipo='lid'` e zero eram `telefone`**. Não é "às vezes o
WhatsApp não manda o número" — é 100%, que é assinatura de defeito, não de
privacidade de usuário.

**A lição de teste.** Havia três casos parametrizados para esse payload:
`{id}`, `{JID}` e `{PhoneNumber}`. Os três assumiam formas **mutuamente
exclusivas**, e o comentário de um deles dizia "endereçamento LID: sem `id`, só
telefone" — a premissa errada, escrita com confiança. O caso real
(`{JID: "…@lid", PhoneNumber: "…"}`, os dois juntos) não existia em teste nenhum.
Cobertura que enumera variantes de um formato precisa incluir a combinação, não
só as alternativas.

**Por que a identidade NÃO mudou de precedência.** A correção mais curta seria
inverter a ordem para `PhoneNumber` primeiro. Não serve: essa string vira
`identificador_hash`, a chave que casa entrada com saída. Trocá-la invalidaria o
pareamento de todos os eventos já gravados — "entraram e ficaram" quebraria em
silêncio. Identidade e telefone passaram a ser **dois campos**, e só o segundo
mudou de fonte.

**A lista de participantes: a segunda inversão de LGPD em dois dias.** O
documento pede "exporta quem está no grupo agora". Não havia fonte: o WAHA
entrega a lista e o código a descartava de propósito (`listar_grupos` afirmava
que os membros "nunca são persistidos"). Sem persistir, o único caminho seria
derivar de `grupo_eventos` — e dos 946 membros do grupo 281, só 472 têm evento.
CSV incompleto sem dizer que está incompleto é pior que CSV nenhum.

O recorte é o mínimo que atende: **só grupo ativado**. Grupo que a afiliada
apenas tem no WhatsApp e nunca ligou continua sendo contagem e nada mais. Isso
mantém a promessa para o caso geral e a quebra só onde ela pediu a
funcionalidade. `PrivacyPolicy.tsx` reescrita no mesmo commit.

**"Cheio" não existia como estado — só como cor.** A rotação sempre respeitou o
limite (`TETO_SQL`, com teste dedicado desde a 079). O que faltava era o grupo
ser **marcado**: `aberto` só virava `false` no ramo em que a campanha inteira
esgotava, e só com `reabertura_automatica=false`, cujo default é `true`.
Resultado: 946/900 com a linha amarela e o toggle "Aberto" ligado para sempre. A
usuária lê isso como "a regra não funciona" — e o diagnóstico dela vira "o limite
não tira o grupo da rotação", que é falso e manda o próximo dev procurar o bug na
query certa.

A varredura de esgotamento passou a gravar `cheio_override` em vez de escrever
`aberto=False`. Escrever `aberto` desfazia a escolha da usuária por baixo, o que
é o mesmo tipo de erro: o sistema mexendo num eixo que é dela.

**O rateio produzia a métrica de destaque a partir de nada.** `_gasto_atribuido`
distribuía o gasto da campanha entre os grupos — proporcional às entradas do
período e, quando **ninguém** entrava, em partes iguais. R$1.223,05 virou
R$611,52 em dois grupos de tamanhos completamente diferentes, e daí saiu "Lucro
por pessoa −R$0,65 / −R$0,92". Ela lê e conclui que os dois grupos dão prejuízo,
quando não há informação para afirmar nada sobre nenhum dos dois. Gasto, lucro e
ROAS passaram a existir só no nível da **campanha**, com o investimento inteiro
entrando uma vez — o que `/resumo` já fazia desde 03/09.

**A Visão geral zerada era a janela, não a escrita.** A suspeita registrada era
"a série foi cortada na troca de número". Descartada estruturalmente
(`grupo_eventos` e `grupo_snapshots` só têm `grupo_id`, nunca `instancia_id`) e
depois nos dados: 100% dos eventos daquela campanha eram do próprio dia, e a
janela fechava no último dia FECHADO em Brasília. O corte existia para não pôr um
ponto pela metade na ponta do gráfico. O preço era maior: campanha nova aparecia
inteira em zero com movimento acontecendo, e zero é uma afirmação — faz a
afiliada concluir que o link não está funcionando. Hoje entra, marcado `parcial`.

**Soft-delete porque o anúncio não para junto.** Hard-delete levaria
`campanha_links` no CASCADE, o slug deixaria de existir e `/g/{slug}` só poderia
responder 404 — enquanto o anúncio já veiculando continua mandando tráfego por
dias. O Meta trata destino 404 como quebrado. Agora responde 200 com "campanha
encerrada". O `excluir()` também cancela as execuções pendentes (não há revoke de
Celery aqui: o cancelamento é por estado, e os dois guards do
`RoteiroEnvioService` já leem isso) e desliga monitoramentos que apontavam para a
campanha — o FK é `SET NULL` e eles continuariam capturando e replicando para
lugar nenhum, em silêncio.

**Cinco itens do documento não eram bugs.** Contador de grupos (cache do
Zustand), bloqueio de remoção de número (implementado desde a 079 — faltava o
frontend reverter após o 409), modal com radio (é Checkbox; o círculo é
`--radius: 0.75rem` com `rounded-sm` numa caixa de 16px, e o defeito é global),
prévia "card verde flutuante" (já era bolha, numa coluna sticky), e `/g/{slug}`
com 404 (funciona em hml; produção não tem o módulo e devolve **200** servindo o
SPA). Investigar antes de corrigir economizou cinco mudanças desnecessárias — e
duas delas teriam introduzido regressão.

Pendente: a **080 tem o mesmo bloqueio jurídico da 079**, e mais forte — publicar
a política antes de aplicá-la em produção. E o checkbox redondo é global: o
`CheckboxQuadrado` foi aplicado só ao módulo de grupos.

---

## 2026-09-04 — Campanhas de grupos: o número do lead, os Números da campanha e o teto

O que mudou: migration **079** (`campanha_numeros` + `campanhas.limite_participantes`
+ `grupo_eventos.identificador`/`identificador_tipo`), aba Números, Visão geral
como painel de leitura, limite de participantes, e a aba Anúncios com gasto,
veiculação real e paginação. Aplicada em **hml**; produção não tem o módulo.

**Por que o identificador tinha prazo.** `grupo_eventos` guardava só o HMAC — e
hash não volta a ser número. A exportação de leads existia e entregava data,
grupo e origem: nada com que falar com quem entrou. Cada dia de campanha rodando
antes da correção era lead perdido para sempre. Como o módulo nunca foi para
produção, deu tempo — mas a decisão de privacidade **inverteu**, e os três
docstrings que prometiam "o número nunca toca o banco" foram reescritos. A
política de privacidade ainda promete o contrário: é bloqueio de promoção.

**O LID.** O WhatsApp manda `84729130@lid` no lugar do telefone quando a pessoa
tem privacidade ativa. Guardar o tipo numa coluna (em vez de adivinhar pelo
sufixo a cada leitura) é o que permite o CSV sair com `telefone` **vazio** nesses
casos. Preencher com o LID daria uma lista de contatos que não existe — e a
afiliada tentaria discar. Célula em branco é a verdade.

**Por que a aba Números não é conforto.** "Adicionar grupos" listava grupos de
todos os números conectados. Grupo do número A numa campanha que dispara pelo B
faz o envio **falhar** — B não participa daquele grupo. A validação do escopo
ficou também no `PUT /grupos` (422): só na tela, o endpoint seguiria aceitando
exatamente o vínculo que quebra o envio.

**A regra de lotação vive num lugar só.** `LEAST(capacidade, COALESCE(limite,
capacidade))` é lida por três queries (escolher grupo, abrir o próximo, fechar os
lotados) e pelo contador "Cheios" da Visão geral. Deixar cópias é como uma delas
fica para trás e o grupo passa a receber gente depois de "cheio", em silêncio —
por isso `TETO_SQL`/`teto_efetivo()` no repository, e um teste que compara o
contador da tela com a decisão da rotação.

**Achado ao aplicar a migration:** hml tem um event trigger `ensure_rls` que liga
RLS em toda tabela nova. Foi ele que protegeu `campanha_numeros`, que o
`create_all` já tinha criado antes da migration chegar. **Não confirmado em
produção** — o runbook de promoção continua valendo por inteiro.

**Achado na validação visual:** o modal de exportar leads cortava em ontem, como
os atalhos do produto. Para lead isso é errado: não é métrica comparável, é
contato, e quem entrou hoje de manhã é quem ela quer chamar agora. Passou a
incluir o dia corrente.

Pendente: política de privacidade antes de `develop→main`; `WHATSAPP_HASH_SALT`
definida antes do primeiro evento em produção.

---

## 2026-09-04 — Primeira promoção para produção por cherry-pick, e o que ela ensinou

O que mudou: a performance do dashboard entrou em produção (`8ffb6f9` backend,
`8e4092f` frontend) **sem merge da develop**. O procedimento virou a seção 9 do
`docs/PROMOCAO_PARA_PRODUCAO.md` e a seção "Branches e deploy" do `CLAUDE.md`
da raiz, que não tinha nada sobre deploy.

**Por que não foi merge.** `git log origin/main..origin/develop` deu **83
commits** no backend e **50** no frontend: módulo de Grupos inteiro, automação
em story, isolamento das filas, duas rodadas de Configurações. Com as migrations
não aplicadas, o merge faria o `create_all` do boot criar as ~20 tabelas do
módulo **sem RLS** em produção — a seção 0 deste runbook descreve exatamente
isso. O pedido era subir um fix, não o backlog.

**Worktree, não `checkout`.** A cópia de trabalho da develop tem 28 arquivos
modificados que não são meus (Campanhas/Grupos em andamento). `git worktree add`
isola o cherry-pick sem tocar neles.

**A armadilha do teste no worktree.** 25 erros de coleção + 18 falhas assustam,
e não são do commit: o worktree não tem `.env` (gitignored) e o `.env` da
develop tem 15 chaves que o `Settings` da main rejeita (`extra_forbidden`). O
que decide é o **controle**: `HEAD~1` deu `18 failed, 369 passed, 25 errors` e
o fix deu `18 failed, 373 passed, 25 errors` — mesma falha dos dois lados, +4
testes novos.

**Confirmação de deploy pelo estado real.** O job verde só diz que o webhook do
Coolify foi aceito. O que vale é `status: finished` **com o SHA empurrado** na
API de deployments (o token está no `.env`, ao contrário do que a memória antiga
dizia) e o hash do bundle do frontend mudando. Medido em produção depois:
`/datasets/all/rows` com o período pedindo **1,84 MB em 2,6 s** (era ~30 MB sem
filtro), KPIs na tela em 3,7 s, cache de 1.782 KB **gravado** — antes nunca
persistia. Números conferidos contra SQL: R$ 8.840,60 e 3.306 pedidos na tela e
no banco.

Pendente: o SHA em `main` é outro, então o merge futuro da develop reconflita em
`datasetStore.ts`, `clicksStore.ts`, `Dashboard.tsx`, `Reports.tsx`,
`ShopeeIntegrationSettings.tsx` e `dataset_row_repository.py` — manter o lado da
develop.

## 2026-09-04 — `list_by_user` consulta colunas, porque o dashboard pedia 67 mil linhas

O que mudou: `DatasetRowRepository.list_by_user` passou a consultar as 19
colunas que a API expõe em vez da entidade `DatasetRow`. **Nenhuma migration**,
nenhuma mudança de contrato — `serialize_row` só lê atributos, e a Row nomeada
responde igual.

**Por que agora.** O dashboard chamava `/datasets/all/rows` sem período: 67.631
linhas na conta do Luiz para exibir 3.882. Com o filtro de data a consulta cai
de **2.018 ms para 14 ms** (`idx_dataset_rows_v2_user_date`, que já existia).
Corrigido o principal no frontend, sobrou o custo por linha do backend — e
materializar 67 mil entidades ORM (identity map, tracking de estado) para
serializar e descartar é o gasto mais fácil de eliminar.

**O que NÃO fiz, e por quê.** Tirar o `response_model=List[DatasetRowResponse]`
economizaria uma revalidação Pydantic por linha, mas o schema tem
`field_serializer` que formata a data como **DD-MM-YYYY** — o formato que o
frontend parseia. Sem o response_model a data sairia ISO e a tela quebraria em
silêncio. Economia pequena, risco desproporcional.

Pendente: o caminho definitivo é **agregar no backend**. Os KPIs que a aluna vê
são calculados no cliente (`get_kpis` não é o que a tela usa), então hoje não há
como responder "comissão do período" sem mandar as linhas.

## 2026-09-04 — Estorno do pedido antigo renomeava a conta de volta e barrava quem tinha acabado de pagar

O que mudou: `find_or_create_user()` ganhou `allow_email_update`; Kiwify e Cakto
passam `action == "activate"`. Regressão em
`tests/unit/test_webhook_rename_email_por_cpf.py`. **Nenhuma migration.**

**O bug não estava na assinatura — estava na identidade.** A aluna Anne comprou
com o e-mail digitado errado (`anne.jesus@hormail.com`), recomprou com o certo
(`annejesus592@gmail.com`) e o pedido velho foi estornado. Os webhooks chegaram
nesta ordem: `order_approved` (errado) → `order_approved` (certo, renomeia por
CPF) → `order_refunded` do pedido 1 → `subscription_canceled` do pedido 1. Os
dois últimos trazem o e-mail do **pedido antigo**, e o rename por CPF era
incondicional: a conta paga voltou para o e-mail errado. No login com o e-mail
certo a lazy migration não achou ninguém e criou uma **segunda conta, sem
assinatura** — e o gate mostrou "Assinatura Necessária" para quem tinha pago 3h
antes.

**Por que ninguém viu.** Não há erro em lugar nenhum: a assinatura existe, está
ativa, paga e válida até 03/10 — só que pendurada em outro `user_id`. Log de
assinatura, painel admin e `subscription_events` mostram tudo certo. O único
rastro é `users.updated_at` bater com o horário do **estorno**, não com o da
compra. Diagnóstico de "paguei e o app pede assinatura" tem que começar
procurando **duas linhas em `users`** para a mesma pessoa (por CPF, e por
`subscription_events.customer_cpf`), não pela `subscriptions` do usuário logado.

**Por que a flag é `activate` e não "não renomeie em cancelamento".** O critério
não é o nome do evento, é a direção: só evento que **libera** acesso carrega a
identidade atual do cliente. Estorno, cancelamento e cobrança atrasada falam
sempre de um pedido que já existia — e podem chegar em qualquer ordem, inclusive
depois da recompra, que foi exatamente o que aconteceu aqui.

Varredura no banco de produção: **só a Anne** foi afetada (nenhuma outra conta
sem assinatura tem `subscription_event` com o próprio e-mail sob outro
`user_id`). Dados corrigidos à mão em produção pelo João em 04/09 — troca de
e-mail entre as contas 75 e 76 e telemetria repontada, sem apagar linha.
Pendente: a correção de código ainda **não está em produção** (sobe com a leva
da `develop`); até lá o caso se repete em qualquer recompra que corrija e-mail.

## 2026-09-04 — Módulo em beta vira flag de runtime, nome do Facebook se auto-cura e pareamento sem QR

O que mudou: `feature_flags.modulos_beta_liberados()` + `modulos` no contexto
de plano; `ad_accounts_names_json` passa a guardar nome **e moeda**, com
`POST /facebook/ad-accounts/resolver-nomes` novo; `codigo_de_pareamento` no
`WahaClient`, no service e em `POST /instancias/{id}/codigo-pareamento`.
**Nenhuma migration** — a coluna da 075 já existia e a mudança é de formato do
JSON dentro dela.

**Por que a flag mora aqui e não no frontend.** O gate por hostname é
build-time: liberar o módulo de disparo em grupo para uma conta de teste em
produção exigia rebuild + redeploy. Aqui é `feature-flags.json` + a env
`MODULOS_BETA` (csv, que **manda sobre o arquivo**) — Coolify + restart. A
distinção "definida e vazia" (fecha tudo) × "não definida" (cai no arquivo) é
proposital: sem ela não haveria como recolher um beta só pelo ambiente. E o
default é **fechado**: módulo ausente do JSON não aparece, porque o default
oposto abriria o módulo para a base inteira por um typo.

**Formato do metadado da conta de anúncio mudou sem migration de dado.** A
coluna passa a aceitar `{"act_1": {"name":..., "currency":...}}` **e** o
formato antigo `{"act_1": "Nome"}`. Reescrever tudo exigiria migration; leitura
estrita apagaria o nome de quem já estava conectada. A tolerância custa um
`isinstance` e evita as duas coisas.

**Resolver nome ficou FORA do `/status` de propósito.** A rodada anterior tirou
a Graph API do carregamento da tela (era o custo que travava conta com muitos
ad accounts) — devolver a Graph para lá agora desfaria isso. Endpoint separado,
chamado uma vez pela tela, depois do primeiro paint e só quando falta nome. E
ele faz **merge**: conta que sumiu da Graph (deixou de ser compartilhada com o
app) continua selecionada e perderia o nome já gravado se o dict fosse
substituído inteiro.

**Pareamento por código: `auth/request-code` do WAHA, com a sessão viva.** O
código só sai com a sessão em `SCAN_QR_CODE`; sessão parada é religada e o
código fica para o toque seguinte — devolver "erro" ali seria mentir sobre algo
que a afiliada não tem como resolver. Número inválido falha **antes** de
qualquer chamada ao WAHA (`NumeroInvalido` → 422), senão "(11) 3222-4444" viria
como `erro: sessao`.

**A sync Shopee não estava quebrada — foi desligada.** Os 24
`shopee-sync-*` estavam `active = false`; o último `job_run_details` é
`succeeded` em 05/08 15:00 UTC e `sync_runs` não tem `cron_incremental` nenhum
depois. Todo o resto do pg_cron do projeto continuava ativo, então não é a
extensão. E o relatório dizia "parada desde 20/08" porque as duas contas com
data recente tinham rodado sync **manual** — a parada real é 05/08, 29 dias.
Produção religada em 04/09 (24/24); hml fica desligada, com só o Luiz (user 9)
agendado pela `078`.

Gotcha do Supabase que vale para toda migration de pg_cron: **`UPDATE cron.job`
dá `42501: permission denied`** (RLS + DML não liberado ao papel do SQL Editor).
Use `cron.alter_job()` ou recrie pelo nome com `cron.schedule`, que faz upsert.
E `SELECT` em `cron.job` pode vir **vazio em vez de erro**, porque a RLS filtra
por `username = current_user` — vazio não significa "não existe job".

---

## 2026-09-03 — Rodada Configurações: dois eixos de "ativo" e o tier que não podia cair

O que mudou: coluna `whatsapp_grupos.ativado` (074) com PATCH próprio, e o
`sub_id`/`custom_link` do grupo migraram do sync para o momento da ATIVAÇÃO;
`rebuild_ad_spend_from_meta` passou a filtrar por conta de anúncio e o
FacebookIntegration ganhou dicionário de nomes (075); colunas `pending_*` em
subscriptions para "maior tier vence" (076); Resumo diário e Blacklist
removidos por inteiro (077 desagenda o cron e derruba as tabelas); janela de
envio nasce DESLIGADA e a regra de borda passou a valer por execução.

Por quê assim:

**`ativo` vs `ativado` não podiam ser a mesma coluna.** `ativo` é lifecycle do
sync — some do WhatsApp vira FALSE, reaparece vira TRUE, incondicionalmente,
todo dia. Se o toggle da usuária morasse ali, o sync da madrugada desfaria a
escolha dela e ninguém entenderia por quê. São perguntas diferentes ("o grupo
existe?" e "eu quero operar nele?") e por isso são duas colunas.

**Atribuição nasce na ativação, e isso não é detalhe de implementação.** O
`sub_id` é o que liga comissão ao grupo. Nascendo no primeiro envio ou na
criação da campanha, todo o tráfego que entrou antes disso perde atribuição de
forma permanente — não dá pra reprocessar. Ativar já entrega o link de entrada
pronto pra anúncio, antes de existir qualquer campanha. Como o sync deixou de
criar (e de backfillar), a invariante "ativado ⇒ tem sub_id" ficou dependendo
de um ponto só; por isso o motor de envio auto-cura quem chegar sem ela, e o
backfill da 074 inclui grupo de monitoramento — não só de campanha, senão
monitoramento vivo morreria calado no deploy.

**"Maior tier vence" num schema de uma linha por usuário.** `user_id` é UNIQUE,
então duas assinaturas simultâneas nunca existiram no estado — o último webhook
sobrescrevia. Quem tinha Max até dezembro e assinasse Pro hoje perdia o Max
pago na hora. A saída foi pendurar a compra menor em `pending_*` e promovê-la
quando a principal expira, na LEITURA (sem depender de webhook novo). O detalhe
que quase passou: `provider_offer_name` continuava apontando pro plano antigo, e
a revalidação de 30 dias reescreve `plan` a partir dele — o downgrade voltaria
um mês depois, longe da causa.

**Tirar a Graph API do mount cobrou um preço escondido.** `GET /ad-accounts`
era, sem que ninguém tivesse decidido isso, o detector de token morto do
Facebook: ao falhar, marcava a integração desconectada. Com a chamada movida
pro modal, o sync virou o único lugar que ainda toca a Graph com regularidade —
e ele engolia o erro e seguia pra próxima conta. Sem mexer nisso, a tela diria
"Conectado" enquanto o gasto parava de entrar.

Migrations 074-077 **aplicadas em homologação em 03/09** (colunas conferidas
uma a uma, 3 tabelas derrubadas, cron desagendado, API religada limpa) e
**pendentes em produção**. A 077 é a que não pode ser esquecida lá: o
`whatsapp-resumo-9am-brt` está agendado em produção e o código que ele chama
deixou de existir. Ela também descarta os opt-ins reais das alunas — em hml
eram 1 opt-in e 6 envios de teste, em produção é dado de gente.

Outras pendências: Contas do Facebook selecionadas antes da 075 não têm nome gravado —
mostram o id até a afiliada re-salvar a seleção no modal. Quem nunca abriu a
aba Envio tinha 08–22 implícito e passa a enviar sem trava de horário: não há
backfill, e vale avisar antes do deploy (risco anti-ban). Pausa por dia já
salva continua no banco, invisível na tela, até a usuária salvar de novo.

---

## 2026-09-02 (noite) — Rodada 9 do painel admin: as 3 causas eram outras

O que mudou: MRR mensaliza sempre ("annually" entrou nos apelidos de
frequência, agora centralizados em `_norm_freq`); bruto do MRR lê
`Commissions.product_base_price` do raw_payload (fallback tabela → bruto
real); "Cancelado pelo produtor" conta churn (exceção segue sendo o par de
upgrade); pareamento de upgrade compara plano NORMALIZADO; backfill desfez 3
flags falsos em produção (Bruna Cabral + par do Deivit; Ana Ariel e Luiz
Fernando mantidos). Na sequência, "Telas mais acessadas" aprendeu as telas
novas (rótulos do menu; /dashboard/admin, /auth e /g/ excluídos do ranking).

Por quê assim: o doc do Luiz apontava sintomas certos com causas erradas — o
diagnóstico assinante a assinante contra produção mostrou que (1) o problema
não era o afiliado: a Kiwify manda "annually" numas assinaturas anuais e
"yearly" noutras, e a lista de apelidos existia em DUAS versões divergentes
(daí a decisão: apelido de frequência vive num lugar só); (2) o webhook manda
`cancel_reason` VAZIO — a exclusão por motivo nunca filtrou nada de webhook;
quem escondia a Bruna do churn era um falso upgrade criado pela comparação de
rótulo cru ("Pro" do import vs "PRO - Mensal" do webhook). Efeitos
retroativos INTENCIONAIS da regra: julho 6→7 canceladas (Deivit, simétrico
com as 7 novas dele) e abril 1→2 (Lara Luiza, produtor do import).

Gotchas de deploy: o `Deploy to Production` do frontend caiu no curl 28
runner→Coolify (intermitência conhecida) — disparado direto na API do Coolify
da máquina local; `CHANGELOG.md` não existia na `main` do backend e o
cherry-pick conflitou (DU) — o arquivo agora existe nos dois branches. A
suíte na `main` coleta com 25 erros AMBIENTAIS (Settings antigo rejeita
chaves novas do `.env` local) — não é regressão. Validação visual com o
supabase.co inalcançável da máquina: gate do /admin é 100% localStorage +
interceptação de rotas no Playwright com JSON real da service.

Pendente: nada da rodada. Avisar o Luiz dos retroativos de julho/abril.

---

## 2026-09-02 (tarde) — Automação em STORY nasce em hml

O que mudou: reply de story vira DM automática. Webhook assina
`comments,messages` e descarta na entrada toda DM que não é reply de story;
pipeline espelha o de comentário (dedupe pelo mid — migration 072 alargou
comment_id p/ 160 e criou `tipo` —, janela 24h da mensageria, teto horário
compartilhado, sem resposta pública). Editor: Card 1 virou toggle
Publicações×Stories (sugestão do João) com seletor de stories ativos.

Por quê assim: pesquisa na doc da Meta ANTES de codar (3 leitores + refutação
adversarial) fixou os limites — story não tem comentário; `messages` não tem
filtro (todas as DMs chegam, o descarte é nosso); arquivo/highlights NÃO
existem na API; republicar exigiria content_publish + App Review (João
descartou). A dúvida que a doc não fechou (`/stories` na variante Instagram
Login) foi fechada na prática: 200 com o story real do João em hml.

Gotcha operacional: o CI de homologação do frontend falhou 2× em silêncio no
passo do Coolify (conectividade runner→IP conhecida) — hml rodou bundle velho
o dia todo e o primeiro teste do João pegou a UI antiga. Deploy disparado
direto na API do Coolify resolve; conferir o CI antes de confiar no hml.

Pendente: promoção para produção (072 em prod ANTES do push; cherry-picks;
assinar `messages` no painel; re-inscrever contas; E2E real com story), e o
preview do editor ainda mostra mock de comentário no escopo story (cosmético).

## 2026-09-02 — Instagram entra em produção (App Review aprovado)

O que mudou: migrations 052–056 aplicadas no Supabase de produção (052 criou
as 3 policies que faltavam — as tabelas já existiam via `create_all` com RLS
sem policy; 053 agendou o cron de token, job 92, e o disparo manual devolveu
202 do backend de produção). Envs `INSTAGRAM_*` gravadas via API do Coolify na
API E no worker + redeploy dos dois. Handshake do webhook: 503 → 403 (token
errado) → challenge em texto puro (token real). Frontend: `main 2d336a8` e
`develop 06a396d`, patches DIVERGENTES de propósito (em main o bloco gated só
tinha Instagram; em develop o bloco é compartilhado com Grupos e foi separado).

Por quê assim: NÃO houve merge develop→main — teria arrastado 61 commits do
módulo de Grupos + migrations 058–071 não aplicadas (o acidente de 22/08 de
novo). O checklist externo pedia o merge; substituído por commits cirúrgicos.

Pendente: swap do webhook no painel Meta (dono do app), E2E com conta MAX,
D+1 do cron, prints do checklist (design).

## 2026-08-31 — Pausar o envio por um número (e por que não virou um `status`)

A tela de Dispositivos ganhou renomear e pausar. Renomear é trivial —
`nome_exibicao` é cosmético e `nome_instancia` (chave do webhook, UNIQUE)
continua imutável. **Pausar foi a decisão de verdade.**

A tentação era `status = "pausada"`: o enum já existia, a coluna já existia,
zero migration. **Estaria errado.** Quem escreve em `status` é o webhook do
WAHA — `aplicar_evento_de_status` e `_marcar_conectada` sobrescrevem o campo a
cada evento de sessão. Uma pausa gravada ali dura até o próximo `WORKING`, e
aí o chip **volta a disparar sozinho**, sem ninguém ter mandado. Falha
silenciosa, do tipo que a afiliada só descobre pelo banimento.

São dois eixos independentes, não dois valores do mesmo campo:

| Campo | Responde | Quem escreve |
|---|---|---|
| `status` | o chip está conectado? | webhook do WAHA |
| `envio_pausado` | a afiliada quer disparar por ele? | a afiliada |

Um número pode estar **conectado E pausado** — e é exatamente o caso de uso:
pausar o chip saudável que está sendo usado demais. O precedente já estava no
repo, no pool de proxies (`068`): `ativo` = intenção humana, `status` = saúde
automática. Migration `070`, duas colunas aditivas.

**Onde a pausa vale e onde não vale.** Vale em
`roteiro_envio_service._instancias_elegiveis` (o pool de envio, o ponto
principal) e em `grupo_evento_service._cliente_do_grupo` (remover alguém por
blacklist é escrita ativa no WhatsApp pelo chip). **Não** vale para
sincronizar grupos, snapshot diário nem monitoramento: leitura e escuta
continuam. Pausar o envio não é desconectar — e a afiliada precisa poder
sincronizar os grupos de um chip que ela pausou.

Os dois caminhos de erro do motor já existiam e cobrem a pausa sem código
novo: pool vazio → `_pausar(execucao, "sem_instancia")` (retomável); grupo sem
candidato → `MSG_PULADA erro="sem_instancia_no_grupo"`.

**Pendências.** A `070` está em hml (aplicada e conferida no banco em 31/08) e
**ausente em produção** — ver `docs/PROMOCAO_PARA_PRODUCAO.md` §1. Ela é
`ALTER TABLE`, então é a armadilha *inversa* da regra do `create_all`: subir o
model antes da migration quebra `GET /instancias` com `UndefinedColumn`, e não
existe "a tabela nasce sozinha" para salvar.

---

## 2026-08-27 — Proxy por sessão no WhatsApp (plano completo, desligado por flag)

Implementado `docs/PLANO_PROXY_POR_SESSAO.md` inteiro, menos o spike que ele
mesmo mandava rodar antes (§5.1) — que precisa de proxy real em hml.

**O que a implementação decidiu além do plano:**

- **A aplicação do proxy numa sessão já pareada é sempre humana**, atrás de
  `WHATSAPP_PROXY_APLICAR_AUTOMATICO=false`. O plano previa realocação
  automática na quarentena, mas isso depende da resposta do spike ("o
  `stop`→`PUT`→`start` pede novo QR?"). Automatizar antes da resposta seria
  arriscar derrubar o número de uma aluna para consertar um IP. Enquanto isso, a
  quarentena realoca **no banco** e grita no log; a sessão segue no IP antigo.
- **Falha de rede em chip SEM proxy continua no disjuntor antigo.** O plano
  isentava `timeout`/`rede` do disjuntor, mas sem IP dedicado não há como
  distinguir "o IP caiu" de "o WAHA caiu" — e a isenção geral faria o lote girar
  em falso, uma linha falhada por vez, até o orçamento da fatia acabar. Hoje
  produção não tem proxy nenhum: essa é a configuração real.
- **O motor alterna entre os chips do mesmo proxy** quando um dá timeout. Sem
  isso, "todos os chips do proxy falharam" nunca seria observável: falha não
  consome cota do dia, então o motor escolheria sempre o mesmo número.
- **`r.motivo_parada` deixou de ser sobrescrito** pelo status genérico da
  execução. `proxy_degradado` virava `pausada` na volta do laço — o diagnóstico
  se perdia justamente no caso em que ele existe para explicar a parada.

**Estado no banco:** migration 068 aplicada em **hml** (a tabela já tinha sido
criada lá pelo `create_all` do backend local, que aponta para hml — a migration
formalizou índice e RLS). 069 (pg_cron da sonda) em **nenhum** ambiente.
Produção intocada.

**Validado em tela** (Playwright, admin em hml): cadastro de proxy, verificação
(host morto → "ConnectError: Connection refused" na linha e no toast, status
ainda `ok` porque 1 falha < 2), realocação de um número com o aviso correto de
que a sessão só usa o IP novo ao reiniciar. Dado de teste removido de hml e a
flag devolvida a `false` no fim.

**O que continua sendo o maior risco de banimento** e NÃO foi feito (plano §7):
aquecimento de chip novo (rampa no `teto_diario`, coluna que já existe),
variação de texto e janela humana. Proxy é o IP; comportamento é o que denuncia.


## 2026-08-26 — O sync que dizia "sucesso" e trazia zero

João conectou o dispositivo dele em homologação, sincronizou e não veio grupo
nenhum. A tela então mandou ele **conectar um número** — o que ele acabara de
fazer. Dois defeitos, e os dois do mesmo tipo.

**`dados if isinstance(dados, list) else []`.** A documentação do WAHA diz, com
essas palavras, que a resposta de `/groups` "depende do engine". O código
respondia a essa incerteza convertendo qualquer formato inesperado em lista
vazia — e `sync_runs` registrou quatro execuções `success` com `vistos=0`, sem
uma linha de log. **Um `else []` diante de um contrato que a própria
documentação diz ser variável não é robustez; é apagar a evidência.** Agora
envelope conhecido é desembrulhado e formato irreconhecível levanta erro.

O mesmo padrão aparecia no `id` do grupo: `str(dados.get("id"))` comparado com
`@g.us`. Se o engine devolve o id como objeto, `str(dict)` nunca casa e TODO
grupo é descartado — de novo, em silêncio. E uma página cheia de itens sem
nenhum grupo reconhecido agora loga as chaves recebidas: se o formato mudar de
novo, dá para descobrir em um minuto em vez de uma tarde.

**O segundo defeito é de leitura de estado.** A tela de campanha decidia o que
mostrar olhando para GRUPOS e concluindo sobre CONEXÃO. Com zero grupos, ela
afirmava "conecte um número". São dois estados diferentes — "sem dispositivo" e
"dispositivo conectado, sem grupos" — e cada um tem uma ação diferente. Colapsar
dois estados num só é o jeito mais rápido de mandar a usuária fazer o que ela já
fez.

**Nota de ambiente:** no meio disso o `.env` foi migrado para as chaves novas do
Supabase e o app parou de subir — `Settings` recusa variável não declarada, e
quatro novas derrubaram a API e a suíte junto. Passou a ignorar desconhecidas.
Em produção esse padrão é pior: env acrescentada no Coolify viraria crash-loop.
Um teste também dependia do `.env` da máquina (`WAHA_WEBHOOK_URL` ausente) e
quebrou quando a variável foi definida — agora ele controla a config.

---

## 2026-08-26 — Auditoria final: o que sobrevive a um teste não é o que sobrevive a um ataque

Seis auditorias independentes sobre o módulo pronto, cada uma seguida de uma
tentativa de refutar os próprios achados. Doze defeitos sobreviveram. **Nenhum
quebrava teste.** O padrão que se repete é o mesmo das fases anteriores, agora
com nome: *o defeito se disfarça de estado de negócio plausível.*

**O pior deles era um comentário confessando o bug.** `identificador()` fazia
`sha256(jid + (salt or ""))` e o próprio docstring dizia: "sem salt ainda
hasheia... embora seja reversível por força bruta; o salt é o que fecha a porta".
A env era opcional e não estava definida em ambiente nenhum. Ou seja: alguém
escreveu a ressalva certa e seguiu em frente. Medir custou 30 segundos —
1,5 M hash/s em Python puro, número recuperado em 0,4 s, espaço inteiro de
celulares BR em ~11 min — e transformou "ressalva teórica" em "a política de
privacidade está mentindo". **Ressalva em comentário não é mitigação; é um bug
com documentação.** Quando o código precisa avisar que faz algo perigoso, o
certo é não deixar o caminho perigoso existir: agora, sem segredo, ele recusa.

**Dependência opcional para uma garantia obrigatória é garantir o pior caso.**
`WHATSAPP_HASH_SALT` ser opcional não foi descuido de configuração — foi um
desenho que só funciona se alguém lembrar. Trocamos por derivação de um segredo
que o app já exige para bootar. A regra que fica: se uma promessa depende de uma
env, ela vai quebrar em algum ambiente.

**O bloco do Dashboard dobrava tudo** com grupo em duas campanhas — e "grupo em
N campanhas" é decisão explícita da F2, escrita no plano. O agregador foi
construído somando por campanha, que é a leitura natural de quem olha uma
campanha por vez. Agregação sobre relação N:N precisa perguntar "o que é a
unidade?" antes de somar; aqui a unidade é o grupo, não a campanha.

**`template_id` vinha do cliente e ninguém checava dono**, nem ao salvar nem no
disparo. É o mesmo furo que já tínhamos fechado para `grupo_origem_id` e
`destino_grupo_ids` na F8 — e passou porque a F4 é mais antiga que a regra.
Vale varrer TODO id que atravessa a fronteira HTTP quando uma regra nova nasce,
não só o código novo.

**Grupo sem nome derrubava a página inteira** e o TypeScript não acusava, porque
o tipo do frontend declarava `nome: string` onde o backend devolve
`Optional[str]`. Tipo que mente é pior que tipo ausente: ele desliga a única
ferramenta que pegaria isso. E o grep por `.toLowerCase` achou cinco pontos; o
sexto (`localeCompare`) só apareceu quando rodei no navegador — **procurar pela
forma do bug encontra menos do que executar o caminho.**

**"Não deu para saber" não pode virar "nada a fazer".** Desligar o monitoramento
com o WhatsApp fora do ar respondia 200. No sentido que protege privacidade, o
silêncio tem que ser um erro alto, não um sucesso otimista.

**Pendência honesta:** a dimensão "operacional" da auditoria não chegou a rodar
(limite de sessão). Refiz as checagens à mão — crons batem com os endpoints,
nenhuma prioridade Celery fora de 0/9, nenhum `func.date` em timestamptz, e as
sete migrations do módulo reaplicam como no-op — mas sem o olhar adversarial
que as outras dimensões tiveram.

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
