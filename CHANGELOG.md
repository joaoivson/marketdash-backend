# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/). Cobre backend
(`marketdash-backend/`) e frontend (`marketdash-frontend/`) juntos — a maioria das
mudanças é cross-stack, então uma entrada única é mais fácil de achar do que dois
changelogs separados.

> **Onde este arquivo vive.** Ele nasceu na raiz do monorepo, que **não é um
> repositório git** — ou seja, ficou 27 entradas sem nunca ter sido versionado.
> Desde 26/08/2026 o arquivo real mora aqui, em `marketdash-backend/CHANGELOG.md`,
> e a raiz tem um symlink apontando para cá. Todos os caminhos antigos continuam
> funcionando; a diferença é que agora existe backup, histórico e revisão em PR.

## [Não versionado] - 2026-09-06 (Roteiros: modelo de tempo, blocos e edição do que já foi agendado)

Documento delta sobre Roteiros. **Migration 082.** Os dois 🔴 do documento
tinham a **mesma causa**, e a investigação no banco de homologação reconstruiu o
minuto inteiro.

### O passo 2 não sumiu: ele foi apagado por um `salvar`

Roteiro "Teste Lançamento", 06/09. O passo 1 (hora fixa, 12:00) saiu no grupo. O
passo 2 (`+5 min`, 12:05) não saiu.

O que o banco mostrou:

| hora (BRT) | o que aconteceu |
|---|---|
| 11:47 | roteiro criado |
| 11:55 | execução 4 agendada |
| **12:00** | tick → `enviando` → **passo 1 sai** ✅ |
| 12:01:40 e 12:01:51 | execuções **5 e 6** — ela clicou "Agendar" mais duas vezes |
| **12:04:39** | os três passos foram **deletados e recriados** ao salvar |
| 12:05:00 | o tick achou **zero** pendentes nas três execuções → `concluida`, `total = 0` |

`PUT /roteiros/{id}/passos` chamava `DELETE FROM roteiro_passos` e reinseria
tudo. `roteiro_mensagens.passo_id` é `ON DELETE CASCADE`: o salvar levou junto a
mensagem que sairia 21 segundos depois. As três execuções terminaram com
`total = 0` — a assinatura da tabela vazia — e nada avisou ninguém.

E os dois bugs se alimentavam: o chip continuava dizendo "Rascunho" e o botão
"Agendar" continuava na linha, o que fez ela agendar três vezes. Se o salvar não
tivesse apagado as mensagens no mesmo minuto, **cada grupo teria recebido tudo
em triplicado**.

**O que mudou.** `definir_passos` faz **diff por id**: passo que chega com `id`
é atualizado no lugar, passo sem `id` é inserido, e só o que sumiu da lista é
apagado. O id preservado é o que mantém a fila viva — e é também o que permite
saber o que já saiu.

### Editar roteiro agendado deixou de ser aceito-e-ignorado

| Passo | Editar | Mover | Excluir |
|---|---|---|---|
| Enviado ou em envio | não | não | não |
| Pendente | sim | sim | sim |

Tentar mexer no que já saiu devolve **409** apontando **quais** passos. Qualquer
alteração aceita **reagenda apenas as pendentes** — o que já saiu não é tocado,
porque a cadeia é relativa: editar o passo 1 recalcula o passo 2, e o passo 1
pode já ter ido.

Cobre os três casos reais: acrescentar passo no fim, corrigir o texto de uma
mensagem que ainda não saiu, e empurrar o resto do lançamento quando algo atrasa.

E agendar duas vezes agora é **409**, com índice único parcial no banco por trás
— a trava que não depende de quem chama.

### A data-âncora global saiu

Abertura de carrinho, virada de lote e fechamento são **data e hora absolutas**.
Não existe offset que resolva, e derivar tudo de uma âncora única não atendia
lançamento. Cada passo de hora fixa passa a carregar a própria data, e os dois
campos são obrigatórios.

O ganho de duplicar continua: em vez de reagendar 22 mensagens, ela ajusta as 4
ou 5 datas fixas — agora numa **tela que lista todas juntas** — e o resto
recalcula pelo offset.

Junto: **offset com unidade** (`+X segundos · minutos · horas`), a linha do
passo mostra o **horário resolvido** (`07/09, 12:10`) e não só `+5 min`, e
**passo no passado bloqueia salvar e agendar**, em vermelho, apontando quais. É
essa trava que sustenta a duplicação — sem ela, duplicar 22 passos e esquecer
uma das datas agenda a mensagem para o lançamento passado.

### Um passo, várias mensagens

Um envio real é frequentemente 4 imagens + um texto saindo juntos. O passo virou
**container de blocos** (`passo_blocos`): compartilham horário e grupos, são
editados e pré-visualizados juntos, e movem juntos na ordem — diferente de criar
5 passos com `+0s`.

Entre blocos entra uma pausa **aleatória de 2 a 5 segundos**, automática: cinco
mídias no mesmo segundo é padrão de robô. E o reenvio **retoma do bloco que
falhou** em vez de repetir os anteriores no grupo.

O editor do passo virou **tela cheia**, com os blocos à esquerda e uma prévia em
bolha de WhatsApp à direita, atualizando enquanto digita. Isso resolve de quebra
o modal que não rolava: o `DialogContent` do shadcn é `fixed` sem teto de
altura, e nos tipos com mais campos o botão "Concluir" ficava fora da tela.
(A correção de rolagem foi aplicada ao `ResponsiveModal` mesmo assim — ela vale
para os outros 28 usos.)

### Ações no grupo

Entram **Alterar a descrição** e **Alterar a imagem**; saem **Abrir entrada** e
**Fechar entrada**. O motivo de saírem é ambiguidade: fecha o quê — o grupo
daquele passo, o toggle "Aberto" da aba Grupos, ou o link de entrada da
campanha? Três controles de nome parecido governando coisas diferentes.

Ação é **exclusiva**: uma por passo, sem blocos. E toda ação exige admin — antes
só `renomear_grupo` era checado no agendamento.

### Status na linha do passo

**Concluído** (verde) · **Concluído com falhas** (laranja, expande e lista quais
grupos falharam) · **Falhou** (vermelho). Antes de rodar, o passo não tem
status. O motivo aparece em português ("Você não é admin deste grupo"), nunca
erro cru.

O status é da **última execução**, não do passo: o roteiro é template e o mesmo
vai rodar no próximo lançamento. Duplicar gera cópia sem status; reagendar
substitui os da execução anterior.

Dentro do passo com falha ela seleciona os grupos e **reenvia** — sempre manual.
Retry automático mandaria a mesma mensagem duas vezes no grupo.

### Salvar sumiu do rodapé

Salvar passa a ser implícito ao concluir o passo, ao mover e ao remover. No
rodapé fica só **Agendar**. Sai o aviso laranja "Salve os passos para a prévia
refletir o que vai ser agendado" — o caminho natural era clicar em Agendar antes
de salvar.

E a lista de grupos virou **checkbox quadrado**: o `rounded-sm` do tema resolve
para 8px numa caixa de 16px, ou seja, um círculo — e círculo numa lista significa
"escolha uma".

### Registrado, sem ação

Com data obrigatória em todo passo de hora fixa, **não existe forma de expressar
"todo dia às 8h"**. Atende lançamento, que é o caso desta rodada; não atende a
fila diária de ofertas. Quando o tipo `oferta` for definido, o roteiro vai
precisar de um modo que repete e de um job que materializa a próxima execução.

O tipo de conteúdo `oferta` **não foi tocado** — continua exatamente como está.

---

## [Não versionado] - 2026-09-05b (Campanhas de grupos: medição, rotação e leads)

Terceiro documento delta, sobre a cadeia de medição. **Migration 081.** Três
itens do documento não se confirmaram e estão registrados no fim.

### "Vagas esgotadas" sai do fluxo normal

Enquanto o anúncio roda, todo clique que caía naquela página era CPC gasto que
não virava lead. Quando todos os grupos estão cheios, o link passa a mandar para
o **primeiro grupo da ordem** — sem tela intermediária. Sempre há gente saindo;
o convite tenta e na maioria das vezes entra.

O fallback ignora `aberto`, `cheio` e o **limite da campanha**, respeitando só a
`capacidade` do WhatsApp — acima dela o convite falha do lado deles, e aí não há
o que tentar. Também não usa `SKIP LOCKED`: no fallback não há vaga a
distribuir, todos vão para o mesmo destino, e a linha travada faria a segunda
requisição simultânea cair em "vagas esgotadas" sem motivo.

O clique **conta**, com `campanha_link_eventos.resultado = 'fallback_lotado'`.
Sem marcar, o gasto existiria no Meta e o clique não existiria aqui — e a taxa
de entrada melhoraria artificialmente justo quando a operação está pior.

"Vagas esgotadas" sobrou para campanha sem grupo utilizável.

### Pausar passou a ter efeito

`pausada` era um select que não desligava nada: `rotear()` só bloqueava
`arquivada` e o motor de roteiros não lia status. Agora o link responde
"campanha pausada" (200, porque o anúncio continua veiculando) e o roteiro para.
Só depois disso o status virou **toggle no cabeçalho** — promover a interruptor
de destaque um controle que não desliga nada seria pior que o select escondido.

### O override de "cheio" não sobrevivia ao sync — e a culpa era da tela

O relato: dois grupos marcados "Cheio = Sim" à mão voltaram para "Não" depois de
uma sincronização. O sync não toca em `cheio_override`; quem apagava era o
próprio clique.

`definirCheio` limpava o override quando o valor escolhido coincidia com o
automático. Marcar "Sim" num grupo **já cheio pela ocupação** gravava `null` —
nada era persistido, a assinatura não mudava, "Salvar ordem" nem acendia, e
nenhum PUT saía. Depois o sync baixava a contagem e o grupo voltava para "Não".

Agora grava sempre, e o Select tem **três estados** (Automático / Sim / Não). A
terceira opção não é enfeite: nada mais limpa o override, e sem ela cada
marcação manual viraria permanente e a "Reabertura automática" passaria a
mentir. O Select exibe a INTENÇÃO; o resultado continua na coluna Ocupação.

**Ocupação em laranja a partir de 90%** — antes a cor seguia `cheio`, então
767/900 (85%) já aparecia alaranjado.

### Evasão de 900%

O grupo #1 tinha 1 entrada e 9 saídas, e a conta era 9 ÷ 1. A base passou a ser
`participantes + saídas` — todo mundo que esteve dentro em algum momento da
janela. É o único denominador que garante saídas ≤ base, ou seja, **evasão nunca
acima de 100%**. Aplicado também na Visão geral: bases diferentes fariam a mesma
campanha mostrar duas evasões na mesma sessão.

### "Leads" era clique, não entrada

1.348 leads conviviam com 53 entradas, e o CPL de R$0,97 ficava ao lado de
R$24,64 de custo por entrada — dois nomes que pareciam a mesma coisa. O pixel
dispara no carregamento da página do `/g/`, antes do redirect: "Lead" do Meta é
**clique qualificado**. Os cards viraram "Cliques no link" e "Custo por clique".

### Lucro negativo sem medição

Comissão, Lucro e ROAS mostram "—" com o motivo quando não há venda rastreada. O
critério **não** é o grupo ter `sub_id` — ele nasce na ativação, sempre, e só
captura se as ofertas usarem os links do MarketDash. Conta como medição: vínculo
manual de Sub ID, ou sub_id de grupo que trouxe pedido de verdade.

### Denominadores explícitos

"Custo por permanência R$32,64" vinha de 1.305,73 ÷ 40, e o 40 não aparecia em
lugar nenhum. Entrou a coluna **Ficaram** na tabela e a nota com o denominador
nos dois cards de custo.

### Telefone com 13 dígitos

1.752 de 2.499 números brasileiros vinham com 12 (sem o 9) — num disparador ou
numa lista de público do Meta, esses falham. Normalizado **no sync** (o valor é
copiado para `grupo_eventos.identificador`, então normalizar só no CSV deixaria
duas representações no banco) e no export, como rede de segurança. Estrangeiros
e telefone fixo passam intactos.

### Sub ID legível

`wgea` não diz nada, e ela vê esse código no relatório da própria Shopee. Grupo
novo nasce `grupo` + nome sanitizado + sufixo — ex. `grupobeatriz2k7f`. Duas
travas: **nunca rederivado** (renomear o grupo não muda o Sub ID) e **só para
grupos novos** (migrar seria perda de atribuição permanente). Os dois formatos
convivem por decisão.

### Atividade

Paginação por **keyset**, não OFFSET: `criado_em` empata em lote (uma entrada de
30 pessoas grava 30 eventos no mesmo instante) e OFFSET repete e pula linhas —
defeito que só apareceria em campanha funcionando. Página de 50 + "Carregar
mais", filtros de tipo e grupo no servidor, erro em português.

**"Saída · origem desconhecida" saiu**: origem é de onde a pessoa veio ao
entrar; saída não tem origem, e o texto fazia parecer que o sistema perdeu
informação que nunca existiu. E o topo passou a dizer a data real
("registradas desde 26/08"), tirada do evento mais antigo — não de
`campanha_grupos.adicionado_em`, porque o grupo pode estar gravando eventos
desde antes de entrar na campanha e a frase se contradiria com a lista abaixo.

### Modal de Sub ID e Configurações

Retry no erro (tratava lentidão como falha terminal), busca por nome, e ordem
por **comissão** — ela procura o que vendeu, não a letra A. Configurações virou
dois blocos com título, o Salvar só acende quando algo mudou, e a descrição da
estratégia "Aleatória" parou de prometer uma comparação que a tela não entrega.

### Dois achados da validação na tela, depois do deploy

**"Custo por entrada R$ 0,00 · 141 entradas"**, com nenhum anúncio vinculado.
O guard olhava só o denominador: com gasto zero a divisão dava 0,0 e a tela
afirmava que cada entrada saiu de graça — a mesma classe do "Lucro
−R$1.305,73" que esta rodada corrigiu. Quatro cards consertados e dois ainda
mentindo, lado a lado. Agora exige numerador **e** denominador.

**Os dois filtros da Atividade apareciam iguais** — "Promos da Beatriz …" e
"Promos da Beatriz …". O `truncate` corta o fim, e o fim é o que separa os
grupos (`#1`/`#2`). Passa a encurtar pelo meio, preservando o sufixo.

E entrou o **aviso ao tirar o último grupo da rotação**, que tinha ficado de
fora: marcar o último como cheio, ou fechar o último aberto, avisa antes e
deixa continuar. Bloquear seria pior — fechar tudo é decisão legítima. Mas a
consequência deixou de ser óbvia nesta mesma rodada: o link não mostra mais
"vagas esgotadas", ele manda para o primeiro da ordem, que já está no limite.

### O item que dois resumos deram por feito

Auditando o documento linha a linha no fim da rodada, apareceu uma
sub-cláusula que eu tinha pulado: o parágrafo do texto da Atividade termina com
**"Some depois de 30 dias."**, e eu implementei só a primeira metade — trocar
"a partir de agora" pela data real.

A frase existe para explicar por que o feed começa onde começa: quem abre uma
campanha de duas semanas precisa saber que o silêncio antes daquela data é
falta de registro, não falta de movimento. Passados 30 dias essa dúvida não
existe mais, e a linha vira ruído fixo no topo de uma tela aberta todo dia.

Daí a regra nova em `.claude/rules/entrega-de-rodada.md` nos dois repos:
**rodada fecha com tabela item a item — OK, pendente ou não implementado —
verificada no código, não de memória.** Este item sobreviveu a dois resumos em
prosa que o davam por completo.

### Cinco regressões que a própria rodada introduziu

Revisão adversarial do diff (6 lentes independentes, cada achado passando por
3 refutadores): 25 achados brutos, 10 sobreviveram, 5 defeitos distintos.
**Todos em código escrito hoje** — nenhum era débito antigo.

**O "Salvar" de Configurações travava para sempre se o `GET /link` falhasse.**
A baseline que decide se há o que salvar só era escrita no caminho feliz; o
`catch` a deixava `null`, e `sujo` exige baseline. A aba virava
editável-e-não-salvável em silêncio: o botão não acendia, o aviso "Alterações
não salvas" não aparecia, e ela sairia achando que gravou. Antes desta rodada o
botão era sempre habilitado e a mesma falha era inofensiva — foi a habilitação
condicional que a tornou grave.

**Pausar pelo cabeçalho apagava as edições não salvas.** O toggle novo vive
fora da aba e troca o objeto da campanha; os efeitos de Configurações dependiam
da identidade dele e reescreviam o formulário. Antes o Status morava dentro do
próprio formulário e não havia controle externo capaz disso.

**"Carregar mais" ficou sem a guarda de resposta obsoleta** que a mesma rodada
criou para o `carregar`. Trocar o chip com uma página em voo anexava o recorte
antigo na lista filtrada — e gravava o cursor dele, que não carrega o filtro
dentro, então as páginas seguintes continuavam no recorte errado.

**Os chips da Atividade vinham do rascunho da aba Grupos**, então o chip de um
grupo adicionado e ainda não salvo mandava um id que o backend recusa com 404 e
a aba inteira trocava a lista pelo card de erro. O `ExportarLeadsModal` logo
abaixo já documentava a regra certa; eu escrevi o inverso ao lado.

**Campanha pausada deixava a execução do roteiro presa em `enviando`.** O guard
novo era o único caminho de parada da fatia que não movia a execução de estado
— e essa é exatamente a assinatura que o tick procura para resgatar execução
cujo worker morreu. Re-enfileirava a cada 5 minutos, gravava um `sync_runs`
bem-sucedido, batia no mesmo guard, e não convergia. Agora parqueia como
`agendada`, e despausar a campanha reagenda para agora.

### O que o documento supunha e os dados contradisseram

| Documento | Medido no banco |
|---|---|
| Export com 87 pessoas a menos = "leitura parcial ou paginação truncada" | **Defasagem**: a lista é do último sync, o contador anda pelo webhook. Fecha na aritmética: `lista + entradas − saídas após o sync = contador − 1` (o −1 é o nosso próprio número, excluído de propósito). O modal passou a dizer a data da lista |
| "A captura funciona, a agregação não" | A agregação está **correta**: 7 dias do grupo #2 = 181 entradas / 68 saídas. O #1 tem 1 entrada porque está cheio — quem recebe é o #2 |
| `custom_link` gravado como URL absoluta | `campanha_links` sempre guardou **só o slug**; a URL é montada na renderização. O domínio errado era a `FRONTEND_URL`, corrigida mais cedo em 05/09 |

E `/g/{slug}` é servida pelo **backend** (`app/main.py`), não pelo React — as OG
tags chegam ao crawler, que não executa JS.

## [Não versionado] - 2026-09-05 (O link de entrada apontava para produção, e a busca de Sub ID varria o histórico)

Três itens reportados depois do deploy da rodada anterior.

### O link de entrada da campanha apontava para o domínio de PRODUÇÃO

O sintoma chegou como "a página do grupo continua não funcionando", com um 404 no
celular. Não era a rota: em homologação a tela mostrava
`https://marketdash.com.br/g/8496c6c7` — domínio de **produção**, onde o módulo
não existe.

`FRONTEND_URL` tinha default fixo em produção, e estava setada explicitamente
como produção nos dois recursos de homologação do Coolify (API e worker). O
`.env` do backend ainda tinha a variável **duplicada**, com a de produção na
última linha — `python-dotenv` monta um dict, então era ela que vencia.

Agora `FRONTEND_URL` é opcional e a base **deriva da ref do projeto Supabase no
`DATABASE_URL`** (`settings.frontend_url`), não de `ENVIRONMENT`: medido na API
de homologação, ela reporta `"development"` — os dois ambientes reportam isso, a
mesma armadilha que fez a fila do Celery ser derivada do banco. Chavear por
`ENVIRONMENT` teria mandado **produção para localhost** no dia em que a env
explícita saísse. Sem banco reconhecido, o fallback é produção, e não localhost:
errar para o domínio público é visível na hora.

Env explícita continua vencendo — é ela que permite domínio próprio — mas quando
não bate com o banco em uso o boot emite um WARNING nomeando o valor esperado.
Antes a incoerência era silenciosa: nada quebrava, o link só levava a lugar
nenhum.

Corrigido também nas envs de homologação; produção conferida e intocada.

### A busca de Sub ID varria o histórico inteiro

`sub_id_sales_summary` agregava `dataset_rows_v2` **sem recorte de período** a
cada abertura do modal de vínculo. O tempo crescia com a conta: quem vende mais
esperava mais, justamente quem mais usa a tela.

Janela de **30 dias**. O modal serve para escolher um sub_id, e sub_id que não
vende há um mês não é o que se está procurando. `dias=0` mantém o histórico para
quem precisar. O rótulo na tela diz o escopo — sem isso a afiliada vê a comissão
cair e acha que perdeu venda.

### O telefone: o webhook do WAHA não manda `PhoneNumber` — está provado

A correção de 04/09b passou a ler identidade e telefone como campos separados no
webhook. **Não bastou**, e agora existe a medida que faltava: dos eventos
gravados em homologação **depois** daquele deploy, 191 continuaram todos
`identificador_tipo='lid'`. Zero telefone. O campo não vem nesse evento.

A leitura no webhook **fica** — ela cobre o dia em que o WAHA passar a mandar o
campo, e removê-la faria o telefone ser descartado de novo. Mas quem resolve
hoje é o payload **REST** de `/groups`, que sabidamente traz `PhoneNumber` (é o
que `_identidades` já lia para descobrir se somos admin). O sync agora, no mesmo
passo em que grava `grupo_participantes`, preenche os eventos que só tinham o
LID — casando pelo `identificador_hash`, que é estável por construção e é
exatamente para isso que ele continuou existindo quando a 079 passou a guardar o
número. O hash não muda; só a coluna exportável.

Idempotente: só toca evento que ainda não tem telefone.

⚠️ **Depende de um número conectado.** Sem sync não há payload REST, e sem ele
não há telefone — nem para os eventos antigos nem para a lista de participantes.

### Correção de teste

`test_excluir_nao_leva_os_grupos_nem_o_sub_id` comparava LISTAS de um
`query().all()` sem `ORDER BY`. O Postgres não garante ordem, e o teste falhava
conforme o plano de execução — flaky escrito por mim na rodada anterior. Passou a
comparar mapa.

## [Não versionado] - 2026-09-04b (Campanhas de grupos e Anúncios: segunda rodada do documento delta)

Segundo documento delta, depois de testar a rodada anterior na tela. Cross-stack,
**com migration 080**. Cinco itens do documento não se confirmaram contra o
código — estão registrados abaixo porque a investigação vale mais do que a
correção que não era necessária.

### O bloqueador: o telefone estava sendo lido do campo errado

A exportação saía com `telefone` vazio em 8 de 8 linhas. A cadeia
`service → repository → model` estava **correta** e gravando; o defeito era uma
linha acima, no webhook (`app/api/v1/routes/whatsapp.py`):

```python
campo(p, "id", "JID", "PhoneNumber", "LID")   # JID vem ANTES de PhoneNumber
```

`campo()` devolve o primeiro nome presente. Em grupo com endereçamento LID o
`JID` **é** `…@lid` e o telefone chega em `PhoneNumber`, ao lado — o próprio
repo documenta isso em `whatsapp_grupo_sync_service._identidades`. Então o LID
sempre ganhava. Medido no banco de homologação: dos 49 eventos gravados depois
do deploy da 079, **49 eram `lid` e zero eram `telefone`**.

O webhook passa a ler os dois como campos **separados**. A identidade continua
com a mesma precedência de antes — é ela que vira `identificador_hash`, e trocá-la
invalidaria o pareamento entrada↔saída de todos os eventos já gravados.

Nenhum teste cobria o caso real: os três parametrizados assumiam formas
**mutuamente exclusivas** (`{id}`, `{JID}`, `{PhoneNumber}`), nunca
`{JID: "…@lid", PhoneNumber: "…"}` juntos. É esse buraco que deixou passar.

### Exportar leads passa a ser "quem está no grupo agora"

Era eventos de entrada dos últimos 30 dias — por isso um grupo com 946 pessoas
acumuladas em meses exportava 8 linhas. A fonte agora é `grupo_participantes`
(080), que o sync mantém com a lista atual de membros.

⚠️ **Inverte a decisão de LGPD de 03/09.** `waha_client.listar_grupos` afirmava
que a lista de membros "nunca é persistida". Não há outro caminho para a
funcionalidade pedida — o recorte é o mínimo que atende: **só grupo ativado**.
A `PrivacyPolicy.tsx` já foi atualizada; publicar antes de aplicar a 080.

Colunas: `telefone · grupo · data_entrada`, sem filtro de período. A data vem do
evento de entrada quando existe e, na falta dele, de quando o sync viu a pessoa
aparecer **depois** de já estar acompanhando o grupo. Quem já estava lá no
primeiro sync sai sem data — inventar uma seria pior.

### "Cheio" e "Aberto" viram dois eixos

`campanha_grupos.cheio_override` (nullable: `NULL` = automático). `aberto` volta
a ser só a decisão da afiliada; quem tira da rotação é `cheio`.

A rotação **sempre** respeitou o limite (`TETO_SQL`, com teste dedicado). O que
não existia era o grupo ser MARCADO: `aberto` só virava `false` no ramo em que a
campanha inteira esgotava, e só com `reabertura_automatica=false` — cujo default
é `true`. Resultado na tela: 946/900 aparecendo "Aberto" para sempre. A varredura
de esgotamento passa a gravar `cheio_override` em vez de desfazer o `aberto` da
usuária.

### O rateio de gasto por grupo foi removido

`_gasto_atribuido` distribuía o gasto da campanha entre os grupos — proporcional
às entradas do período e, quando **ninguém entrava**, em partes iguais. Foi como
R$1.223,05 virou R$611,52 em dois grupos de tamanhos completamente diferentes, e
produziu "Lucro por pessoa −R$0,65 / −R$0,92" — justamente a métrica de destaque
do módulo.

Gasto, lucro, ROAS e lucro por pessoa agora só existem no nível da **campanha**,
com o investimento inteiro entrando uma vez (o mesmo que `/resumo` já fazia). A
comissão por grupo continua na linha: essa é real, vem do Sub ID do grupo.

### Sub IDs vinculados à campanha

Tabela `campanha_sub_ids` (N por campanha, contra 1:1 de Anúncios). Bloqueia o
que já entra por outro caminho — Sub ID de grupo da campanha, de campanha de
tráfego direto, ou de outra campanha de grupos — e a soma deduplica, porque a
mesma comissão em duas pontas é o problema que a regra existe para evitar.

### Tela de Anúncios: campanha de grupo não tem comissão

Campanha vinculada a grupo é rastreada pelo link de entrada, não pelo Sub ID.
Ela entrava em "Lucro −R$5.084,43" e "ROAS 0.41x" como prejuízo puro.

- **Gasto continua somando tudo** — é o número conferido contra o Meta.
- **Lucro e ROAS Real excluem o PAR** (gasto *e* comissão) dessas campanhas: só
  o gasto sairia e o ROAS de uma campanha com Sub ID viraria infinito.
- Card e dia a dia ganham conjunto próprio: `Gasto · Leads · CPL · CPC · CTR` e
  `Data · Gasto · Impressões · Cliques · CTR · CPC · Leads · CPL`. Sem o bloco
  "Anúncios × Shopee", que depende do Sub ID.
- `leads`/`cpl` entram em `CampaignMetrics` e `CampaignDailyPoint`. `None` ≠ `0`:
  soma com `+=` cru apagaria a distinção "sem pixel" × "ninguém virou lead".
- Somem o aviso "não vinculada" e o botão "Vincular ao Sub ID" quando há grupo.
- Os alertas do topo ("sem vínculo", "ROAS abaixo de 1") passam a ignorá-las —
  contagem e filtro com o **mesmo** predicado, senão o banner diz "3" e a lista
  mostra 5.

### "Onde seu anúncio está rodando" foi removido

Mostrava ROAS 9,25x no Instagram enquanto o KPI do topo da mesma tela mostrava
0,41x. Não era problema de ambiente: o card mede ROAS como faturamento/gasto — a
fórmula que o "ROAS Real" abandonou de propósito — e ignora os dois impostos.
Estava atrás de `!isProductionHost()`, o que só adiava; já tinha subido para
produção uma vez.

### Visão geral: a janela excluía o dia de hoje

O gráfico fechava no último dia FECHADO em Brasília. Campanha que começou hoje
aparecia com Entradas e Saídas em **zero** com movimento acontecendo — foi
exatamente o caso reportado: 18 eventos gravados no dia, gráfico reto. Confirmado
no banco: 100% dos eventos da campanha eram de 04/09.

Hoje entra, e o último ponto vem marcado `parcial` — a tela rotula "· hoje" para
o dia em curso não ser lido como queda.

### Excluir e duplicar campanha

**Excluir** é soft-delete (`status = encerrada`). Hard-delete levaria
`campanha_links` no CASCADE, o slug deixaria de existir e `/g/{slug}` só poderia
responder 404 — enquanto o anúncio já veiculando continua mandando tráfego por
dias. Agora responde **200** com "campanha encerrada". O `excluir()` também
cancela as execuções de roteiro pendentes (não há revoke de Celery no módulo: o
cancelamento é por estado) e desliga monitoramentos que apontavam para ela — o FK
é `SET NULL` e eles continuariam capturando e replicando para lugar nenhum.

**Duplicar** copia configuração, prévia, pixel e números; **não** copia grupos,
anúncios nem Sub IDs. Os dois últimos são invariantes de dinheiro:
`campanha_anuncios.campaign_id` tem UNIQUE global, e copiar levantaria
IntegrityError — se não levantasse, contaria o mesmo gasto duas vezes.

### Resto da rodada

- **Configurações vira aba** (era o único elemento de navegação da campanha fora
  da barra de abas). Nome, duplicar e excluir foram para a listagem; "Link ativo"
  veio da aba Link. Os dois switches de abertura ganharam descrição.
- **Listagem**: "Enviar oferta" removido (envio rápido é roteiro de um passo e
  pertence à campanha), menu de três pontinhos, e o contador de grupos corrigido.
- **Aba Grupos**: coluna "Envio" removida, filtros Todos/Cheios/Não cheios,
  seleção em lote e o teto vindo pronto do backend.
- **Números**: o 409 passa a reverter a seleção — a tela ficava com o checkbox
  desmarcado, "Alterações não salvas" aceso e "1 grupo nesta campanha" embaixo,
  que se lê como "o bloqueio não funcionou".
- **Link de entrada**: três blocos, prévia colapsada e vazia por padrão (o link
  vai em anúncio, onde o Meta usa a criativa dele), preview dentro do mesmo card.
  Toggles PageView/Lead removidos — **backend e frontend**: só apagar os toggles
  deixaria linha antiga com `false` apagando o Lead em silêncio.
- **Checkbox quadrado** (`CheckboxQuadrado`): `--radius: 0.75rem` + `rounded-sm`
  numa caixa de 16px dá raio igual a metade do lado — um círculo. O controle
  sempre foi checkbox; o que enganava era a borda. Aplicado ao módulo de grupos;
  os demais usos do app continuam redondos.
- **Byte NUL** literal dentro de `assinatura()` em `LinkDeEntradaDaCampanha.tsx`
  (`].join("\0")`), que fazia o `grep` tratar o arquivo como binário e suprimir
  a saída sem erro — quem investigasse o componente o veria vazio.

### O que o documento supunha e o código contradisse

| Documento | Código |
|---|---|
| `/g/{slug}` retorna 404 | Funciona em hml. Em produção o módulo **inteiro** é develop-only (nem a rota, nem o `location /g/` do nginx) — e o "404" é HTTP **200** servindo o SPA |
| Contador de grupos lê fonte diferente | As duas fontes concordam. Era cache do Zustand (`loaded && !force`) — corrigido com SWR |
| Bloqueio de remoção de número não implementado | Implementado desde a 079, com testes. O sintoma era o frontend não reverter após o 409 |
| Modal "Adicionar grupos" com radio | Já era `<Checkbox>` multi-select. O defeito é o raio da borda, e é global |
| Prévia é "card verde flutuante" | Já era bolha de WhatsApp; o problema era ela morar numa coluna sticky separada |
| Rateio dividiu igualmente entre 2 grupos | Só quando **ninguém** entra no período; com entradas é proporcional. O defeito é o mesmo, mas são dois caminhos |

## [Não versionado] - 2026-09-04 (Campanhas de grupos: rodada de correções do documento delta)

Documento delta do Luiz sobre Visão geral, Números, Grupos e Anúncios, depois de
um teste real da tela. Cross-stack, **com migration 079**.

### O bloqueador: exportar lead com hash não serve para nada

`grupo_eventos` guardava só `HMAC-SHA256(segredo, jid)` — irreversível por
desenho. A exportação de leads existia, mas entregava data, grupo e origem, sem
nenhuma forma de falar com quem entrou. E evento gravado como hash **não volta a
ser número**: a correção precisava chegar antes de qualquer campanha rodar em
produção.

Agora o evento guarda `identificador` (o JID como veio) e `identificador_tipo`.
O hash **continua** — é ele que casa entrada com saída ("entraram e ficaram") e é
dele que o índice `ix_ge_ident` depende.

**O WhatsApp nem sempre manda telefone.** Quem tem privacidade ativa chega como
LID (`84729130@lid`), um id opaco que não disca. Por isso o tipo é uma coluna, e
não uma adivinhação pelo sufixo a cada leitura: no CSV, LID sai com a coluna
`telefone` **vazia**. Preenchê-la com o LID daria uma "lista de contatos" que não
existe — pior do que a célula em branco, que é a verdade. Evento anterior à 079
também sai vazio, pelo mesmo motivo de sempre: só tem o hash.

⚠️ **Bloqueio de promoção:** a política de privacidade ainda promete o contrário.
Precisa ser atualizada antes de `develop→main`.

### A aba Números, que não existia

O sintoma era pior do que "falta configuração": "Adicionar grupos" listava grupos
de **todos** os números conectados. Um grupo do número A numa campanha que
dispara pelo B faz o **envio falhar** — B não participa daquele grupo.

- Nova aba **Números** (nome travado — nunca "Dispositivos"/"Instâncias") com
  seleção múltipla; `campanha_numeros` guarda o vínculo.
- A aba Grupos e o modal "Adicionar grupos" passam a oferecer só grupos desses
  números. Sem número escolhido, a aba explica e aponta para Números — não fica
  uma lista vazia sem motivo, que se lê como bug.
- A regra vive **no backend** também (422 no `PUT /grupos`): só na tela, o
  endpoint continuaria aceitando o vínculo que quebra o envio.
- Desmarcar número com grupos na campanha é **bloqueado** (409), nomeando os
  grupos e dizendo onde resolver. Sem reassociação automática — complexidade sem
  retorno, e silenciosa.
- Backfill na migration: campanha que já existe adota os números dos grupos que
  já tem.

### Visão geral deixa de ser formulário

Era a primeira tela da campanha e não dizia nada sobre ela: nome, descrição e
toggles. Virou painel de leitura com link de entrada copiável, KPIs operacionais
(cliques, entradas, taxa, saídas, evasão, participantes), gráfico de entradas ×
saídas com 7/14/30 dias e o estado dos grupos.

Sem métrica financeira: comissão, lucro e ROAS são de Resultados. E `null` ≠ `0` —
taxa sem clique mostra "—", não "0%", que afirmaria "ninguém converteu".

A edição foi para um botão **Configurações** no topo. **Descrição saiu da UI**
(a coluna fica no banco); era campo em branco na primeira interação.

### Limite de participantes por campanha

`whatsapp_grupos.capacidade` existia (1024) e **nunca era escrita**. Faltava o
que a afiliada queria: encher só até 900, porque grupo perto de 1024 fica lento e
ela quer margem para quem entra fora do link.

`campanhas.limite_participantes` (NULL = sem limite próprio). O teto efetivo é
`LEAST(capacidade, COALESCE(limite, capacidade))` — a capacidade continua sendo o
teto absoluto. A expressão vive **em um lugar só**: a regra de lotação é lida em
três queries (escolher, abrir o próximo, fechar os lotados), e o dia em que uma
cópia ficar para trás o grupo recebe gente depois de "cheio", em silêncio.

A coluna Participantes virou **ocupação** (`944/900`), com destaque ao atingir o
limite. O contador "Cheios" da Visão geral usa a **mesma** expressão — um número
com regra própria diria "há vaga" num grupo que o roteador já não escolhe.

### Anúncios: dava para ver o nome, não dava para escolher

Três campanhas com nome **idêntico** (`[TRAFEGO] [SHOPE GRUPOS] [31/03/26] [CBO]`)
eram indistinguíveis na hora de vincular. Agora a linha traz **gasto no período**,
com seletor 7/14/30/mês, ordenada por gasto (era alfabética — jogava
"alvejantepo1805" acima de campanha com R$800).

- Filtro **Ativas · Pausadas · Todas**, abrindo em Ativas.
- "Ativa" é **veiculação real** (`_is_active` + `ad_review_issue` +
  `_still_delivering`), não `effective_status` — que fica ACTIVE para sempre em
  campanha com orçamento vitalício esgotado. Mesma regra do card de campanhas
  ativas.
- Lista só as contas selecionadas em Configurações › Facebook Ads. **Mas anúncio
  já vinculado nunca some da lista**, nem por filtro nem por conta desmarcada:
  senão a afiliada perde a única forma de desvincular e o gasto segue entrando no
  lucro sem ela poder ver por quê.
- Paginação 25/50/100.

O multi-select **já funcionava** — o print sugeria radio, mas é `Checkbox` com
`Set<number>` desde sempre, e o `PUT` recebe lista. O que existe de verdade é o
UNIQUE por `campaign_id` (um anúncio do Meta pertence a uma campanha de grupos),
que é proposital e fica.

### Grupos: exclusão sem confirmação e envio no lugar errado

- O `×` removia direto. Virou menu de três pontinhos + confirmação, dizendo que o
  grupo continua ativo e nas outras campanhas.
- **"Enviar oferta" saiu** e foi para Roteiros, ao lado de "Novo roteiro": envio
  rápido é roteiro de um passo, e manter o botão em Grupos sugeria um caminho de
  envio paralelo ao motor de roteiros. Aparece inclusive sem nenhum roteiro — é
  justamente quem ainda não montou um que mais usa a ação.
- **Exportar leads** com seleção de grupos. O período aqui **inclui hoje**, ao
  contrário dos atalhos do produto: lead não é métrica comparável, é contato, e
  quem entrou hoje de manhã é quem ela quer chamar agora. Descoberto na validação
  — a primeira versão cortava em ontem e escondia os leads mais quentes.

### O que a revisão pré-commit pegou

Nove defeitos confirmados (de 28 levantados; 11 refutados na verificação), todos
corrigidos antes de subir. Os que mais importam:

- **`POST /campanhas-grupos` daria 500 em toda criação de campanha.** Tirar
  `descricao` do schema deixou a rota lendo `payload.descricao`, e o Pydantic v2
  não guarda campo que não declarou: `AttributeError`, que nenhum `except` da
  rota pega. A suíte passava porque os testes chamavam o **service**, nunca a
  rota — agora há um teste que exercita o endpoint.
- **`PUT /numeros` dava 500** quando a campanha tinha um número que a afiliada
  removeu da conta: o soft delete tira a instância de `por_usuario` mas não de
  `campanha_numeros`, e o código indexava o dicionário.
- **409 falso ao desmarcar número.** O bloqueio olhava presença, não órfão: com
  dois chips no mesmo grupo, tirar um deles era impossível sem esvaziar a
  campanha — exatamente o caso de quem vai aquecer um chip.
- **"Disponíveis" mentia.** Contava aberto + vaga, mas o roteador também exige
  `ativo` e `link_convite`: o painel dizia "1 disponível" enquanto quem clicava
  no link via "vagas esgotadas".
- **Participantes divergia entre abas da mesma campanha.** A Visão geral
  preferia o snapshot da madrugada; Grupos e Resultados leem o contador vivo.
- **`PUT /anuncios` devolvia o gasto de outra janela**, com o chip da tela ainda
  marcando "7 dias".
- **A seleção do export voltava para "todos"** a cada re-render do pai, e usava
  os grupos do rascunho não salvo (422 do backend).

### Migration 079

`campanha_numeros` (+ backfill), `campanhas.limite_participantes`,
`grupo_eventos.identificador` e `identificador_tipo`. Aplicada em **homologação**;
produção não tem o módulo. Também no boot-ALTER de `db/base.py`.

Achado ao aplicar: hml tem um event trigger `ensure_rls` que liga RLS em toda
tabela nova — foi ele que protegeu `campanha_numeros`, criada pelo `create_all`
antes da migration. **Não confirmado em produção**: o runbook continua valendo.

### Testes
`tests/unit/test_campanha_numeros_e_limite.py` (13 casos): lotação pelo limite,
capacidade como teto absoluto, contador da tela igual à rotação, bloqueio de
número com grupos, escopo dos grupos, telefone × LID. Mais 3 no export.
1081 passando.

### Fora do escopo, encontrado no caminho
`docker-compose.yml` tinha três linhas duplicadas (chave `redis` repetida) que
impediam qualquer comando `docker compose` — corrigido.

## [Não versionado] - 2026-09-04 (Performance do dashboard: pedir o período, não a base inteira)

Relato do Luiz: "demora muito carregar esse dashboard… coisa de minuto".
Cross-stack, **sem migration**. **Em produção desde 04/09** — subiu por
cherry-pick em `main` (backend `8ffb6f9`, frontend `8e4092f`), sem merge da
`develop`, que tem 83 + 50 commits de módulos ainda não promovidos.

### O que estava acontecendo
O dashboard chamava `/datasets/all/rows` **sem período** e filtrava no
navegador. Medido em produção, na conta dele:

| | linhas | JSON | tempo no banco |
|---|---|---|---|
| o que era baixado | 67.631 | ~30 MB | **2.018 ms** |
| o que a tela usa (7 dias) | 3.882 | ~1,8 MB | **14 ms** |

O índice `(user_id, date)` já existia — ninguém o usava. Somam-se a isso 67 mil
objetos serializados no backend, o gzip de 30 MB, o `JSON.parse` no navegador e
um `JSON.stringify` de 30 MB que **sempre falhava**: acima da cota do
localStorage (5–10 MB por origem) o `setItem` lança, o `catch` engolia — o
cache nunca persistia justamente na conta grande, e toda carga era carga fria.

### Correção
- **Frontend:** `datasetStore` manda `start_date`/`end_date` (o `clicksStore` já
  mandava). Cache de vendas e cliques passa a ser **por usuário + período**
  (`dataset-cache:2026-08-28_2026-09-03:user_9`) e só grava até 8.000 linhas.
  "Limpar filtros" rebusca — sem período significa histórico inteiro, e agora
  quem corta por data é a API. Parâmetro de data pelos componentes **locais** da
  data, não por `toISOString()` (o UTC mandava um dia diferente do escolhido).
- **Backend:** `DatasetRowRepository.list_by_user` consulta **colunas** em vez
  de entidades ORM — não materializa um objeto por linha. Sem mudança de
  contrato: mesmos campos, mesmo formato de data.
- Testes: `tests/unit/test_dataset_rows_por_periodo.py` (recorte por data,
  isolamento por usuário, serialização campo a campo).

### Medido em PRODUÇÃO depois do deploy (conta com 67.139 linhas)
`/datasets/all/rows?start_date=…&end_date=…` devolve **1,84 MB em 2,6 s** (era
~30 MB sem filtro nenhum); os KPIs aparecem **3,7 s** depois do dashboard abrir;
e o cache do período foi **gravado** (1.782 KB) — antes o `setItem` estourava a
cota e toda carga era fria. Números conferidos contra o banco: a tela mostra
**R$ 8.840,60** de comissão e **3.306** pedidos em 28/08–03/09, idêntico ao SQL
aplicando as regras do KPI (fora UNPAID e cancelados).

### Medido antes, em homologação (52.372 linhas, conta `relacionamento@`)
Janela de 19 dias: **2,75 MB em 2,3 s**. Sem período: **8,7 s** só na request,
~15 s até a tela. Os números conferem com o SQL: 01/08–19/08 dá **R$ 13.457,00
de comissão e 4.916 pedidos** na tela e no banco (descontando UNPAID e
cancelados, que é a regra do KPI).

### O que continua lento — de propósito, por ora
**Relatórios** (default "Todo período", 9,9 s em hml) e **Impostos** (monta a
lista de meses a partir do histórico) seguem pedindo tudo. A saída definitiva é
agregar no backend — hoje os KPIs são calculados no cliente —, não baixar linha.

## [Não versionado] - 2026-09-04 (Fix: estorno do pedido antigo desfazia a troca de e-mail e barrava assinante pagante)

Incidente relatado pela aluna Anne (`annejesus592@gmail.com`): comprou, definiu
senha, usou o dashboard à tarde e à noite passou a bater em **"Assinatura
Necessária"**, sem saída. Backend apenas.

### Causa raiz
`find_or_create_user()` renomeava a conta sempre que achava o usuário **pelo
CPF** — inclusive em evento de estorno/cancelamento, que chega com o e-mail do
**pedido antigo**. A sequência real dos webhooks da Kiwify foi:

1. `order_approved` com o e-mail digitado errado (`anne.jesus@hormail.com`) — cria a conta;
2. `order_approved` da recompra, com o e-mail certo — acha por CPF e renomeia (certo);
3. `order_refunded` do pedido 1, com o e-mail **errado** — acha por CPF e **renomeia de volta**;
4. `subscription_canceled` do pedido 1 — idem.

A conta paga terminou com o e-mail errado. No login com o e-mail certo, a lazy
migration não achou ninguém e criou uma **segunda conta, sem assinatura** — o
gate do frontend (`status.is_active`) então bloqueou quem tinha acabado de pagar.
O sintoma é silencioso: a assinatura existe e está ativa, só que pendurada em
outro `user_id`.

### Correção
- `find_or_create_user()` ganhou `allow_email_update` (default `True`): só
  evento que **libera** acesso pode trocar o e-mail da conta pelo CPF. Quando a
  flag está desligada e o match veio por documento, o e-mail atual é mantido e a
  divergência vai para o log.
- Webhooks Kiwify e Cakto passam `allow_email_update=(action == "activate")`.
- Regressão em `tests/unit/test_webhook_rename_email_por_cpf.py` cobrindo a
  ordem real dos eventos (recompra e depois estorno do pedido velho).

### Dados em produção
Correção de código não desfaz o estado já gravado. As duas contas da Anne foram
mescladas à mão em **04/09**: os e-mails trocaram de lugar (a conta 75, que tem a
assinatura, o CPF e o nome, ficou com o gmail; a 76 ficou com o e-mail do typo e
sem assinatura) e a telemetria foi repontada para a 75. **Nada foi apagado.**
Varredura no banco confirmou que ela era a **única** conta afetada.

Diagnóstico deste sintoma — "paguei e o app pede assinatura" — começa procurando
**duas linhas em `users`** para a mesma pessoa (por CPF e por
`subscription_events.customer_cpf`); a `subscriptions` do usuário logado mostra
apenas "não tem assinatura", que é verdade e não ajuda.

## [Não versionado] - 2026-09-04 (Configurações — Operação › Parâmetros, gate por flag e os 5 bugs de produção)

Segunda rodada do documento delta do João, sobre a de 03/09. Cross-stack.
**Nenhuma migration nova**: 074-077 continuam sendo as pendentes para produção.

### Navegação (§ Navegação lateral)
- Quinta seção: **Operação › Parâmetros**. "Envio" sai de dentro do WhatsApp e
  vira tela própria — janela de envio não é configuração de canal, vale para a
  operação inteira, e como aba de WhatsApp ficava invisível para quem entra
  pelo módulo de Campanhas. Estrutura final:
  `CONTA` (Assinatura) · `INTEGRAÇÕES` (Marketplaces, Facebook Ads, Instagram,
  WhatsApp) · `OPERAÇÃO` (Parâmetros) · `CÁLCULOS` (Impostos).
- **WhatsApp fica só com Números, sem abas internas**, e a página do número
  perde a `TabsList` de uma aba só. Uma aba solitária não é navegação, é
  moldura — volta quando existir a segunda.
- `?tab=envio` passa a cair em **Parâmetros** (o deep-link antigo não podia
  virar um WhatsApp sem abas). Grupo de navegação que fica sem seção nenhuma
  some inteiro — nada de cabeçalho "OPERAÇÃO" sozinho.

### Densidade (§ Global)
- `SecaoCard` reapertado como régua única: título `text-sm`, `p-3.5` (padding
  igual no mobile e no desktop — o `md:p-4` fazia as duas densidades
  divergirem), header `mb-2.5`, caixa do ícone 32px. Ganhou slot `action`, e o
  card de Marketplaces — que tinha cabeçalho próprio, ícone de 48px e título
  `text-base` — passou a usá-lo: era a única seção fora da régua.
- Critério de aceite medido por screenshot: em **Operação › Parâmetros os 7
  dias abertos cabem sem scroll em 1366×768 e 1280×720**, com o botão Salvar
  na tela. Antes: 777px de conteúdo em 720 de viewport.

### Subida para produção — gate por FLAG, não por hostname (§ Subida)
- `isProductionHost()` sai do gate do módulo de disparo em grupo. Era
  **build-time**: liberar o módulo para uma conta de teste em produção exigia
  rebuild + redeploy, e a spec pedia beta sem redeploy.
- No lugar, `feature-flags.json` ganha `modulos_beta`, o backend resolve por
  **conta** (`liberado` / `planos` / `emails`) e devolve a lista em
  `GET /subscription/plan` → `modulos`. A env `MODULOS_BETA` (csv) manda sobre
  o arquivo: liberar ou recolher em produção é variável no Coolify + restart.
- Frontend: `usePlanStore.moduloLiberado()`, `menuVisivel()` filtrando por
  `item.modulo` e um `RequireModulo` novo nas rotas. As rotas do módulo agora
  **existem sempre** e o wrapper decide depois que o contexto chega — o
  `{cond && <Route/>}` fazia link direto cair em 404 por uma fração de segundo
  antes de a rota passar a existir.
- **Fecha por padrão**: módulo ausente do arquivo, contexto ainda carregando ou
  backend antigo sem o campo → invisível. O default oposto abriria o disparo em
  grupo para a base inteira por um erro de digitação no JSON.
- Com a flag fechada sobra exatamente o que a spec pediu: Assinatura ·
  Marketplaces · Facebook Ads · Instagram · Impostos, e `/dashboard/grupos`
  redireciona para `/dashboard` em vez de 404.

### Marketplaces
- **Autofill (o `10020` voltou).** A rodada anterior tratou `autocomplete` e
  ids, e não bastou: o Chrome ignora `autocomplete="off"` quando decide que o
  formulário é de login e preenche o **primeiro par usuário/senha** que
  encontra — que era App ID + Secret. Agora os campos se chamam
  `ref_publica`/`ref_secreta` (nenhuma heurística de login casa), carregam
  `data-lpignore`/`data-1p-ignore`/`data-form-type="other"`, e um par
  `username`/`password` invisível e fora da ordem de tabulação abre o
  formulário para absorver o preenchimento. Vale nos **dois** formulários — o
  de adicionar conta e o da engrenagem de sincronização.
- **Sync parada virou estado visível.** O limiar caiu de 24h para 6h (a sync é
  horária) e o "sync atrasada" cinza de 12px virou badge âmbar com o tamanho do
  atraso escrito: *"Sincronização parada há 14 dias"*. O tamanho é a
  informação — 14 dias e 7 horas são problemas diferentes. Aparece também na
  faixa da sync legada (estado vazio e conta órfã).

### Facebook Ads
- **Regressão do nome da conta.** A lista voltou a exibir
  `act_266908603365617` cru porque o metadado só nascia no momento da SELEÇÃO:
  quem conectou antes da coluna existir (ou reconectou depois, o que reescreve
  a integração) não tinha como recuperar o nome sem reabrir o modal e
  re-salvar. Agora:
  - `ad_accounts_names_json` passa a guardar **nome + moeda**
    (`{"act_1": {"name": ..., "currency": ...}}`), com leitura tolerante ao
    formato antigo só-nome — reescrever exigiria migration de dado, e leitura
    estrita apagaria o nome de quem já estava conectada;
  - `POST /facebook/ad-accounts/resolver-nomes` resolve pela Graph e **faz
    merge**: conta que saiu da Graph (deixou de ser compartilhada com o app)
    continua selecionada e mantém o nome que já tínhamos;
  - a tela chama isso **uma vez, depois do primeiro paint e só quando falta
    nome**. O `/status` segue sem tocar na Graph — era o custo que a rodada
    anterior tirou do carregamento.
  - A lista e o modal voltam a mostrar `Nome` + `id · MOEDA`.

### WhatsApp › Números
- **Pareamento por código** (`POST /instancias/{id}/codigo-pareamento` →
  `auth/request-code` do WAHA). A aluna abre o MarketDash no celular e o
  WhatsApp que ela vai conectar é o do mesmo aparelho: não há como escanear o
  QR da própria tela. Sem isso, parte do público não conecta e vira churn
  silencioso. O modal novo (`ConectarNumeroModal`) oferece os dois caminhos e
  mantém o poll do QR nos dois modos — é ele que detecta a conexão.
  Número inválido falha **antes** de falar com o WAHA, com mensagem própria.
- **"Desconectado" e "Aguardando conexão" deixam de mentir a mesma frase.**
  Os dois exibiam "Número ainda não pareado"; quem já tinha pareado e caiu lia
  que nunca havia pareado. `desconectada` sem número agora diz *"Conexão
  perdida — reconecte para voltar a enviar"*.

### Detalhe do número › Grupos
- **Paginação** (25 por página, seletor 25/50/100, "1–25 de 493") no lugar do
  scroll infinito, que não dava como voltar a um grupo que passou.
- **Filtro de estado** `Ativos (N) · Todos (N)`, abrindo em **Ativos** —
  paginar sozinho não resolvia: com 25 por página e 493 grupos, os 2 ativos
  ficavam espalhados por 20 páginas.
- Linha mais baixa (`py-1`) e nome em **peso normal**: em bold, 25 nomes
  seguidos viram um bloco só e nenhum se destaca.
- **Nomes idênticos ganham desambiguador.** Dois `#130 SALESDASH + VENDE-C`
  (347 e 2 participantes) são grupos distintos — padrão de grupo sucessor.
  A linha passa a mostrar o fim do JID (`…203779` / `…902488`), e **só onde o
  nome se repete**: em 493 linhas, sempre seria ruído. A data que guardamos é a
  do primeiro sync, igual para os dois, então não diferencia nada.
- **Estado vazio da busca**, com a saída: quem busca dentro de "Ativos" quase
  sempre queria buscar em "Todos", e o botão leva pra lá.

### Operação › Parâmetros
- Recebe o conteúdo de WhatsApp › Envio num bloco só (Janela de envio); nada de
  bloco vazio prometendo o que ainda não existe.
- Duas regras viram texto na tela, porque são a diferença entre "não enviou" e
  "quebrou": execução que começa dentro da janela é concluída mesmo passando do
  fim, e **a janela global é teto** — campanha marcada para 08:00 com a janela
  abrindo 09:00 espera a abertura, não envia às 08:20.
- A descrição do switch muda com o estado: desligado não existe teto, e dizer
  "os envios pausam" nesse caso seria mentira.

### Assinatura
- **Contador "1 · Ilimitado" morre.** Ilimitado exibe só *"Ilimitado"* — sem
  teto, o consumo não mede coisa nenhuma. Com teto continua `usado/limite`
  (`Números 2/3`).
- **Linha "Grupos ativos" sai.** O Max vai ter teto, só não definido ainda;
  publicar "Ilimitado" agora seria promessa a retirar depois. Volta junto com o
  número.

### Sync Shopee parada — causa encontrada e produção religada
- **Os 24 `shopee-sync-*` do pg_cron estavam `active = false`.** Não quebraram:
  o último `cron.job_run_details` é `shopee-sync-12h-brt` em **05/08 15:00 UTC,
  `succeeded`**, e `sync_runs` não tem nenhum `cron_incremental`/`cron_full`
  depois disso. Todo o resto do pg_cron (facebook, grupos-snapshot, instagram,
  proxy, roteiros) seguia `active = true` — só a família Shopee caiu. O Vault
  estava certo (`backend_base_url` = PROD, a 036 vale).
- **A data do relatório estava errada: parou em 05/08, não 20/08 — 29 dias.**
  19 contas têm `last_sync_at` cravado em `2026-08-05 15:00`; as duas datas
  recentes eram sync **manual**. O "14 dias" na tela era a idade do último
  clique em "Sincronizar agora" da conta de teste. Para saber se o cron vive, o
  sinal é `sync_runs.trigger`, não a data da tela.
- **Produção religada em 04/09** (24/24 `active = true`, lista batendo com a
  migration 030 — sem drift). **Homologação fica desligada** de propósito, com
  só a conta do Luiz Fernando agendada: `078_shopee_sync_por_usuario.sql` cria
  `trigger_shopee_sync_user(user_id, sync_type)`, porque a `trigger_shopee_sync`
  original sincroniza todo mundo e não dava para escopar.
- **`UPDATE cron.job SET active = true` não funciona no Supabase**
  (`42501: permission denied for table job` — a tabela tem RLS e o DML direto
  não é liberado ao papel do SQL Editor). O caminho é `cron.alter_job()`, que é
  SECURITY DEFINER, ou recriar pelo nome com `cron.schedule` (que faz upsert).
  E `SELECT` em `cron.job` pode vir **vazio em vez de erro** — a RLS filtra por
  `username = current_user`. Vale para toda migration de pg_cron daqui pra
  frente.

### Testes
- 21 testes novos (`test_modulos_beta.py`, `test_facebook_nome_e_moeda.py`,
  `test_codigo_pareamento.py`), suíte em **1059 passando**.
- Validação por screenshot em 1366×768, 1280×720 e 390×844 contra homologação
  com dado real (493 grupos, 3 contas de anúncio, sync Shopee parada há 14
  dias) — inclusive o comportamento com a flag fechada.

## [Não versionado] - 2026-09-03 (Configurações — reestruturação da tela, toggle de grupos e upgrade de periodicidade)

Documento delta do João ("Correções da tela de Configurações"). Mexe em
navegação, densidade, integrações, WhatsApp, assinatura e remove duas features.
Cross-stack. **Migrations 074, 075, 076 e 077 aplicadas em HOMOLOGAÇÃO em
03/09/2026 e PENDENTES em produção** — as quatro vão junto com o deploy desta
rodada; protocolo no cabeçalho de cada arquivo e estado por ambiente em
`docs/PROMOCAO_PARA_PRODUCAO.md`. Atenção à **077 em produção**: ela desagenda
o pg_cron `whatsapp-resumo-9am-brt` (ativo lá) e dropa `whatsapp_optins` /
`whatsapp_envios` / `blacklist_numeros`, que em produção têm dado real de
aluna — exportar antes, se o histórico importar.

### Navegação e densidade (§0, §1, §2)
- Sub-navegação passa a ter 4 seções: **Conta** (Assinatura) · **Integrações**
  (Marketplaces, Facebook Ads, Instagram, WhatsApp) · **Cálculos** (Impostos).
  Somem o grupo "Dispositivos" e o item "Canais" — Facebook e Instagram viram
  itens próprios (estavam na mesma tela por motivos opostos: um traz dado pra
  dentro, o outro age pra fora), e WhatsApp vira integração como as demais,
  com **Números** e **Envio** como abas irmãs. Deep-links antigos
  (`?tab=shopee|canais|numeros|envio|bloqueios|resumo`) continuam abrindo o
  lugar certo, e o gate `isProductionHost()` do WhatsApp segue valendo.
- Régua de densidade única: `components/shared/SecaoCard.tsx` (título
  `text-base`, descrição `text-xs`, `p-4 md:p-5`). Mudar a densidade agora é
  um arquivo, não sete telas — era o jeito de não desalinhar as abas entre si.
- Texto de ajuda longo deixa de ser fixo na tela e vira link discreto que abre
  modal. Vale como regra da plataforma, não só destas telas.

### Marketplaces (§3)
- A etiqueta **"principal"** sai da lista. O conceito não existe mais (são
  múltiplas contas resolvidas pelo marketplace da URL), mas o *label* continua
  sendo a chave de upsert no backend e o que distingue contas extras — então o
  badge só some quando o nome é o default.
- **Autofill corrompendo credencial (bug real):** o Chrome lia App ID como
  e-mail e preenchia com o login da aluna, que salvava sem ver e recebia erro
  `10020` sem causa aparente. Agora `autocomplete="off"`/`"new-password"` e ids
  que não disparam a heurística (`shopee-open-api-id` / `-key`). A validação de
  App ID numérico passou a acusar no blur, não só no submit.
- Link "Não sei onde pegar / não tenho ainda" abaixo da senha, abrindo o passo
  a passo (incluindo como pedir a ativação da API e o aviso de que ela aparece
  sozinha na tela "Abrir API" — não chega por e-mail).

### Facebook Ads (§4)
- Microcopy: "Traz o gasto dos seus anúncios pra dentro do MarketDash."
- **A tela não chama mais a Graph API ao abrir.** `GET /ad-accounts` é ao vivo
  e paginado (contas + BMs); rodava a cada abertura de Configurações e, em
  conta com muitos ad accounts, empurrava o resto pra baixo da dobra e travava
  o carregamento. Agora o estado padrão mostra só as contas **selecionadas**
  (nomes persistidos em `ad_accounts_names_json`, migration 075) e a lista
  completa carrega ao abrir o modal de seleção, com busca e aplicação em lote
  (1 PUT + 1 sync, em vez de um por clique).
- **Conta não marcada não grava gasto:** `rebuild_ad_spend_from_meta` passou a
  filtrar a agregação por `Campaign.ad_account_id`. Antes, insight histórico de
  conta desmarcada continuava sendo reprojetado no AdSpend a cada sync.
- Como a listagem saiu do mount, o sync virou o **único detector automático de
  token morto** — então ele passa a marcar a integração como desconectada ao
  receber `FACEBOOK_TOKEN_INVALIDO`, em vez de seguir para a próxima conta. Sem
  isso a tela diria "Conectado" enquanto o gasto parava de entrar, em silêncio.

### Instagram (§5)
- Bloco de 3 passos enxuto, com o **alerta do passo 2 preservado** ("Permitir
  acesso às mensagens"): sem essa permissão a Meta não envia o webhook de
  comentário e a automação fica muda, sem erro — é a principal causa de chamado
  insolúvel. O caminho de menu completo foi para o modal "Onde encontrar cada
  passo"; o parágrafo sobre senha saiu.

### WhatsApp — Números e Grupos (§6)
- Números viram **grid de cards compactos** (status, nome, número mascarado,
  contagem de grupos, pausa, ações). A lista de grupos sai de dentro do card:
  clicar abre `/dashboard/configuracoes/numeros/:id`, com abas (Grupos, hoje)
  — estrutura pronta para histórico de envio e saúde do número.
- Tabela de grupos com **3 colunas**: Ativo · Nome · Participantes. As colunas
  "Envio" e "Também em" saíram (sempre vazias, sem significado pra usuária).
- **Toggle "Ativo" por grupo (migration 074, coluna `ativado`).** Conectar o
  WhatsApp pessoal sincroniza *tudo* — no teste, 492 grupos para ~6 de
  trabalho. Só grupo ativado aparece na seleção de destino, é monitorado e
  conta no limite do plano; o resto fica no banco, silencioso e sem custo.
  **Grupo nunca é deletado** — desativar é a única forma de tirar da operação,
  e o histórico de comissão permanece em Campanhas › Resultados.
  - `ativado` (escolha da usuária) é eixo **separado** de `ativo` (lifecycle do
    sync, que reativa incondicionalmente toda madrugada). Guardar o toggle em
    `ativo` faria o sync desfazer a escolha dela na noite seguinte.
  - **Ponto de atribuição:** `sub_id` (`wg`+base36) e `custom_link` passam a
    nascer **na ativação**, não no sync nem no primeiro envio — ativar já
    permite usar o link de entrada em anúncio antes de existir campanha. Se
    nascessem depois, todo tráfego anterior perderia atribuição de forma
    irrecuperável. A geração é idempotente (sub_id existente nunca é
    regenerado) e o motor de envio auto-cura quem chegar sem ela.
  - Backfill da 074 liga `ativado` para grupo já vinculado a campanha **e para
    grupo que é origem de monitoramento** — senão o monitoramento morreria em
    silêncio no dia do deploy.
- Botão "Conectar número" desabilitado passa a dizer por quê, com caminho de
  upgrade quando há tier acima.
- Número criado e nunca pareado expira em 24h, removido via
  `WhatsappInstanciaService.remover` (deleta a sessão no WAHA e libera o proxy;
  UPDATE direto deixaria sessão zumbi consumindo RAM).

### WhatsApp — Envio (§7)
- **"Restringir horário de envio" agora vem DESMARCADO** (backend e frontend):
  restrição virou opt-in. ⚠️ Quem nunca abriu a aba tinha 08–22 implícito e
  passa a operar sem trava — não há backfill.
- Configuração por dia colapsada, com resumo "Todos os dias · 08:00 – 22:00",
  link "Personalizar por dia" e ação **"Aplicar a todos os dias"** com rótulo
  em texto (o ícone de copiar sozinho não era descoberto). Os 7 dias cabem em
  1366×768 e 1280×720 sem scroll.
- Controle "Pausa" por linha removido (redundante com o toggle do dia).
- **Regra de borda:** execução que começa dentro da janela é concluída, mesmo
  ultrapassando o fim. A unidade é a *execução*, não a fatia — um lote grande
  gasta várias fatias, e checar por fatia pararia às 22:05 um envio começado às
  21:50, com metade dos grupos sem a oferta. Os tetos de volume (240/dia do
  plano, 5000 global, 80 por instância) continuam por mensagem.

### Assinatura (§10)
- **Bug crítico corrigido:** a marcação de "Plano atual" ignorava a
  periodicidade, então quem estava no Max Mensal via o card Max desabilitado
  também nas abas Trimestral e Anual e **não conseguia fazer upgrade de
  período**. Já aconteceu com aluna real — era perda de receita ativa. Agora
  casa plano + periodicidade, e o mesmo plano em outro período fica habilitado
  como "Mudar para trimestral/anual". Sufixo de preço corrigido para
  `/mês`, `/trimestre`, `/ano` (imprimia os ids crus).
- **Duas assinaturas vigentes concedem o maior tier** (migration 076, colunas
  `pending_*`). `subscriptions.user_id` é UNIQUE, então "duas assinaturas" não
  existia no estado: o último webhook sobrescrevia e quem comprasse Pro tendo
  Max vigente perdia na hora o que já pagou. A compra de tier menor agora fica
  pendurada e é promovida quando a principal expira. A promoção limpa
  `provider_offer_name` — a revalidação de 30 dias o usa para reescrever o
  plano e reverteria o tier promovido um mês depois.
- Contador de uso por limite na tela de Assinatura (links, páginas, números,
  grupos ativos), com "Ilimitado" para `-1` e travessão para limite `0`. As
  contagens usam **a mesma regra dos gates de criação** (links excluem os
  internos de grupo) — divergir faria a aluna ver 29/30 e tomar erro de limite.
- Período passa por `_norm_freq` na saída da API: apelido cru da Kiwify
  ("annually") faria nenhum card casar e a assinante veria "Assinar" no plano
  que já paga.

### Removidos (§9)
- **Resumo diário** (WhatsApp às 9h): tela, item de menu, rotas, services,
  models e o job. O valor real é o alerta de campanha abaixo do breakeven, e
  isso pertence ao Dashboard, onde ela age. Sai junto a aba admin do QR dessa
  sessão. `POST /whatsapp/webhook` e o tratamento de status/participantes
  **ficam** — servem o módulo de grupos.
- **Bloqueios (blacklist)**: sem caso de uso enquanto grupos não estiver em
  produção. Volta quando os grupos subirem (bloquear concorrente que entra no
  grupo para capturar ofertas).
- Migration 077 desagenda o `whatsapp-resumo-9am-brt` no pg_cron e derruba as
  tabelas — só remover o código deixaria o job batendo em 404 para sempre.

### Verificação
1038 testes unitários passando; `tsc --noEmit` limpo; build ok; lint sem erros
novos. Validação visual via Playwright em 1366×768, 1280×720 e 390×844 (sem
overflow horizontal em nenhuma tela). Conferido em homologação com conta MAX
Mensal: as três abas de Planos, o contador de uso, o grid de números, a página
de detalhe do número e a criação de `sub_id`/`custom_link` na ativação.

## [Não versionado] - 2026-09-02 ("Telas mais acessadas" aprende as telas novas)

Pedido do João na sequência da Rodada 9. O mapa rota→nome do ranking
(`NOMES_DE_TELA`, `platform_usage_service.py`) tinha parado no tempo: tela
desconhecida caía num nome gerado do path ("Automacoes", sem acento) e rotas
renomeadas não eram reconhecidas. Agora:

- Todas as telas atuais entram com o MESMO rótulo do menu lateral: Instagram,
  Campanhas (WhatsApp), Planos, Indique & Ganhe, Integrações, Impostos,
  Ofertas, Templates, Módulos. Rotas renomeadas apontam pro mesmo nome da
  legada (`/dashboard/links` = Meus Links, `/dashboard/captura` = Página de
  Captura, `/dashboard/reports` = Relatórios) — nada duplica no ranking.
- `/dashboard/campanhas` agora aparece como **Anúncios** (o menu renomeou;
  "Campanhas" hoje é o módulo de grupos de WhatsApp).
- Excluídos do ranking: `/dashboard/admin/*` (painel interno), `/auth/*`
  (fluxo de senha) e `/g/*` (página pública de link de grupo, irmã de /l/ e
  /c/) — apareciam via fallback e não são uso do produto.

Validado contra os page_views reais de produção (30d): Planos 62, Indique &
Ganhe 57, Instagram 57 já aparecem nomeados. Só backend; sem migration.

## [Não versionado] - 2026-09-02 (Painel Admin — Rodada 9: MRR "annually", churn do produtor, gráfico)

Doc do Luiz de 25/08 (3 itens). Nada tinha sido implementado (nem em develop);
implementado e no ar nos 2 ambientes. Testes de aceite validados contra os
dados reais de produção antes do deploy (diagnóstico assinante a assinante).

### Item 1 — MRR não mensalizava (R$651 inflados)

A causa NÃO era o afiliado em si: a Kiwify manda a frequência da assinatura
anual como **"annually"** em parte dos webhooks (João Victor e Alice) e como
"yearly" em outros — e `_freq_divisor`/`_norm_freq` só conheciam
yearly/annual/anual. "annually" caía no ramo mensal e o líquido inteiro
(R$292,41 / R$417,73) entrava no MRR sem dividir por 12. Excesso somado:
R$650,96 — exatamente os R$651 do doc.

- Apelidos de frequência agora vivem num lugar só: `_norm_freq`
  (`app/core/plans.py`, ganhou "annually" e "quarter"); `_freq_divisor` delega;
  `plan_frequency_distribution`, labels do CSV de clientes
  (`_CSV_FREQUENCY_LABELS`), `translateFrequency` do frontend e o fallback de
  `plano_periodo` no webhook Kiwify alinhados (os três últimos: achado da
  revisão adversarial — mostravam "annually" cru).
- **Bruto do MRR lê `Commissions.product_base_price`** do raw_payload do último
  evento pago (campo real da venda) — não presume preço de tabela; fallback:
  tabela → bruto real pago. Import histórico (sem o campo) segue na tabela.
- Card validado em produção: líquido R$2.016,08 · bruto R$2.289,00 · ARPU
  R$48,00 (aceites eram 2.019,62/2.291,00/48,09 na base de 25/08 — a diferença
  é a base ter mudado: Vivian cancelou 27/08 e houve vendas novas). João Victor
  contribui R$24,37 líq / R$37,25 bruto ✓. Líquido < bruto ✓.

### Item 2 — "Cancelado pelo produtor" conta churn

Duas correções, porque o motivo NUNCA foi o filtro real em produção (o webhook
da Kiwify manda `cancel_reason` vazio — a exclusão por motivo só pegava import):

- Exclusão por `cancel_reason` removida de `cancel_instants()` e
  `churn_for_month()` — produtor é saída real. Exceção continua sendo upgrade
  (`is_plan_change`). Efeito retroativo intencional: Lara Luiza (produtor,
  abril, import) passa a contar — abril vai de 1 → 2 canceladas no gráfico.
- **Bruna Cabral estava fora do churn por um FALSO upgrade**: o pareamento
  comparava `plan_name` cru, e "Pro" (import) vs "PRO - Mensal" (webhook)
  parecia plano diferente ≤30d. Agora compara plano normalizado
  (essencial/pro/max) + frequência normalizada. Backfill
  (`scripts/backfill_rodada9_plan_changes.py`, rodado em prod e hml) desmarcou
  3 eventos: Bruna (24/08) e o par do Deivit (31/07, mesmo falso-par — julho
  vai de 6 → 7 canceladas, simétrico com as 7 novas que ele já contava);
  Ana Ariel e Luiz Fernando (upgrades reais) mantidos fora.
- Churn de agosto: **8** (os 7 do aceite + Vivian, que cancelou 27/08, depois
  do doc). `renewal_rate` herda a regra: produtor no ciclo desfaz renovação.

### Item 3 — Gráfico Novas × canceladas

- Meses sem movimento no início da série saem (`trimLeadingNoMovement`, mesma
  lógica do corte do MRR/DRE) — abril/2026 em diante.
- `margin={CHART_MARGIN}` (o mesmo dos gráficos vizinhos, que faltava aqui) dá
  o respiro no topo — label "32" da maior barra visível inteiro.

Commits: backend `develop` + cherry-pick em `main`; frontend idem. Sem
migration. Suíte: 1006 testes ✓. Validação visual (Playwright, dados reais de
produção interceptados): MRR/ARPU/gráfico conferidos em tela.

## [Não versionado] - 2026-09-02 (Comentário de story: sem API — dica no editor)

Pergunta do João depois do E2E: dá para automatizar também o COMENTÁRIO de
story (o "Manda 🔥" que aparece na aba Comentários do story)? **Não — limitação
da Meta, verificada empiricamente**: o comentário de story não gera webhook
nenhum (o evento de comments no mesmo minuto era de um post da
promosdabeatrizz_, coincidência), o objeto dá 400 na Graph API
("does not support this operation") e não existe campo de webhook para isso na
lista exaustiva do painel. Só a RESPOSTA (reply) chega — como DM, e essa a
automação já pega.

Consequência de produto: **o CTA do story precisa pedir RESPOSTA, não
comentário** ("Responda com QUERO") — o CTA errado mata a automação em
silêncio. Dica adicionada ao Card 1 do editor para os dois escopos de story
(`develop a0104d8` / `main 6d6da74`). Reavaliar se a Meta um dia expor um
campo novo de webhook para comentários de story.

## [Não versionado] - 2026-09-02 (Hotfix: mid real estourou o VARCHAR — 073)

O PRIMEIRO reply de story real em produção (11:52 BRT, @imagineteen__ →
"Manda 🙏") revelou que o mid da Meta tem ~180+ caracteres — os 160 da 072
não bastaram: o INSERT caía em `StringDataRightTruncation` e a task ficava em
retry (a cada 120s, máx. 3) sem a DM sair. **O ALTER para VARCHAR(512) foi
aplicado à mão em prod e hml segundos antes do último retry** — que então
gravou `story_reply|enviado` às 11:58:21 e a DM chegou. Migration **073**
versiona o que já está nos bancos; model e teste de regressão atualizados
(nota: o SQLite dos testes NÃO valida length de VARCHAR — por isso a suíte
não pegou; quem valida é o Postgres). Commits: develop e `main 0a33571`.

Cadeia real completa validada em produção: story → reply de outra conta →
webhook `messages` → fila → pipeline → Send API → **DM entregue**.

## [Não versionado] - 2026-09-02 (Automação em STORY — no ar em homologação)

Pedido do João de manhã ("dá pra selecionar um story também?"), no ar em hml à
tarde. Pesquisa na doc da Meta (3 leitores + verificação adversarial) antes de
codar; a implementação reaproveita o pipeline de comentário inteiro.

### O que existe agora (hml)

- **Reply de story → DM automática.** Story não tem comentário: o gatilho chega
  pelo webhook `messages` (assinamos `comments,messages`; o webhook descarta DM
  comum/echo/reação no primeiro filtro). Escopos `story_especifico` (morre com o
  story em 24h) e `story_qualquer`. Sem resposta pública (não existe em story).
  Dedupe pelo `mid` (migration **072** alargou `comment_id` p/ 160 e criou
  `tipo`), janela de 24h da mensageria, teto horário compartilhado.
- **Card 1 do editor virou toggle Publicações × Stories** (sugestão do João —
  4 opções empilhadas escondiam as de story abaixo da dobra), com seletor de
  stories ativos (`GET /instagram/stories`), thumbs 9:16 e aviso de validade.
- **fix (também em produção, `main 1ce08d2`)**: a barra Salvar/Publicar cobria
  o sidebar (fixed inset-x-0 → `md:left-72`) e ganhou o botão **Voltar**.
- Simulador ganhou `--story`. Testes: 108 Instagram / 986 unit, verdes.

### Validações (02/09 à tarde)

- E2E simulado em hml: sem_match ✅ · match→Send API real (Meta recusou o token
  sintético com 190, provando o caminho) ✅ · dedupe por mid ✅.
- **`GET /me/stories` FUNCIONA na nossa variante** (graph.instagram.com +
  instagram_business_basic): com a conexão real do João em hml devolveu 200 com
  o story ativo dele (media_product_type=STORY) — a doc da Meta era ambígua
  nesse ponto; confirmado na prática. O story apareceu no seletor.
- Playwright em hml (toggle/opções/cards) e em prod (barra x=288 + Voltar).
- ⚠️ CI de homologação do frontend falhou 2× no passo "Trigger Coolify
  deployment" (conectividade runner→IP conhecida) — deploy disparado direto na
  API do Coolify. Conferir o CI antes de confiar que hml atualizou.

### O que NÃO entrou (decisão do João, 02/09)

- **Stories arquivados**: a API da Meta não expõe (nenhum endpoint, nem
  highlights) — confirmado adversarialmente na doc.
- **Republicar story**: descartado (exigiria instagram_business_content_publish
  + App Review novo + mídia re-hospedada).

### ✅ PROMOVIDO PARA PRODUÇÃO em 02/09 (~10h50 BRT), autorizado pelo João

Executado na ordem: 072 aplicada em prod (colunas medidas) → backend
`main 6eeb8b8` (cherry-pick de d05e53d; 601 testes verdes no código promovido;
worker reiniciou com `processar_story_reply_instagram_task` no banner) →
frontend `main 8bb872b` (cherry-picks de 18d7045+3e172dd; tsc baseline 26) →
**3 contas ativas re-inscritas** com `subscribed_fields=comments,messages`
(joaoivsonn, promosdabeatrizz_ e achadinhosdalua00 — a terceira conectou
sozinha em 02/09) → Playwright em prod: toggle/opções OK, `GET /stories`
devolveu 200 com o story real, zero 5xx.

**Único passo restante (manual, dono do app):** assinar o campo `messages`
no painel de Webhooks da Meta — sem ele a Meta não ENTREGA as DMs ao app,
mesmo com as contas inscritas. Depois: E2E real (responder um story de outra
conta → DM).

### Checklist da promoção (como foi planejado)

1. Aplicar a **072** em produção ANTES de qualquer push na main (ALTER TABLE —
   armadilha inversa do create_all).
2. Backend: promover `d05e53d` (develop→main é proibido; cherry-pick).
3. Frontend: promover `18d7045` + `3e172dd` (o fix da barra `624f0ac` já está
   na main como `1ce08d2`).
4. Painel Meta (dono do app): **assinar o campo `messages`** no webhook.
5. Re-inscrever as contas conectadas de produção (`/connection/subscribe` ou
   aguardar o refresh diário) para incluir `messages` no subscribed_fields.
6. E2E real: responder um story de outra conta → DM.
7. Pendência menor: o preview do editor ainda mostra o mock de comentário no
   escopo story (cosmético).

## [Não versionado] - 2026-09-02 (Mobile: Instagram no drawer "Mais" + nota botão×texto)

- **fix(mobile) `main 65862fe`**: em `main`, a sidebar e o `MobileBottomNav`
  têm listas separadas (a config única `dashboard-menu.ts` é só do develop).
  O gate foi aberto na sidebar (2d336a8) mas o nav mobile nunca teve o item —
  celular em produção ficou sem caminho até `/dashboard/automacoes`. Agora
  "Automação Instagram" está no drawer "Mais" (validado em prod, viewport
  390px, navegação ok, zero 5xx). develop não precisa do fix (deriva do
  `DASHBOARD_MENU`); no futuro merge, manter a versão de develop.
- **Esclarecimento botão×texto na DM** (não é bug): a regra do backend é o
  PAR — link+botão = template `button`; link sem botão / botão sem link =
  422 com mensagem dizendo o que falta; **os dois vazios = DM de texto puro**
  (`instagram_login_client.py:381-395`). Para texto puro, limpar os DOIS
  campos do Card 4. `INSTAGRAM_DM_FORMATO=texto` segue sendo só o interruptor
  global de emergência.

## [Não versionado] - 2026-09-02 (Plano Max lançado na página de vendas)

Com o Instagram no ar, o Max saiu do modo "só por link direto da Kiwify" e
entrou na vitrine — landing (`SalesPrecos.tsx`, 3º card dourado com badge
"Novo") e `/dashboard/planos` (`PLAN_ORDER` ganhou `"max"`). Preços que já
existiam no catálogo dos dois lados: **R$ 97/mês · R$ 207/trimestre (−29%,
eq. R$ 69/mês) · R$ 627/ano (−46%, eq. R$ 52,25/mês)**. Os teasers do toggle
subiram para "até 29%"/"até 46%" (os maiores descontos agora são do Max).

- A copy do Max vende **Automação de Instagram + páginas/links ilimitados** e
  NÃO menciona o módulo de grupos de WhatsApp, que segue fora de produção.
- Nenhuma mudança de backend: `PRICES`/`CHECKOUT_LINKS` do Max já estavam em
  produção desde o merge de 22/08; o modal de upgrade já oferecia Max pelo
  cadeado. Foi só vitrine.
- Commits: `develop 60931a6`, `main b0eefd5` (cherry-pick limpo — os dois
  arquivos eram idênticos entre os branches).
- Conta `relacionamento@` (user 1) promovida a Max direto no banco de
  produção em 02/09 (`UPDATE subscriptions SET plan='max' WHERE user_id=1`,
  era `pro`) para validação — reverter quando quiser.

## [Não versionado] - 2026-09-02 (Instagram EM PRODUÇÃO)

A Automação Instagram (comentário → direct, exclusiva do MAX) saiu de
homologação e entrou em produção na noite de 01→02/09. O que destravou: o
**App Review da Meta foi aprovado em 01/09** (instagram_business_basic,
instagram_business_manage_comments, instagram_business_manage_messages, todas
Advanced Access; as 6 permissões do Facebook foram renovadas juntas).

### O que foi feito (na ordem)

- **Migrations 052→056 aplicadas no Supabase de produção** (via psql com a
  connection string de produção). Antes, medido: 3 tabelas criadas pelo
  `create_all` com RLS ligado e **zero policies**; depois: 1 policy `*_iso`
  por tabela, cron `instagram-token-refresh-diario` agendado (`15 5 * * *`,
  job 92). Disparo manual da função devolveu **202 background-inline** do
  backend de produção — cadeia Vault → pg_net → endpoint validada.
- **Envs `INSTAGRAM_*` criadas via API do Coolify** na API de produção E no
  worker (o direct sai pelo worker): APP_ID, APP_SECRET, OAUTH_REDIRECT_URI
  (`https://marketdash.com.br/dashboard/automacoes/callback`), WEBHOOK_VERIFY_TOKEN
  (mesmo de hml — o app Meta é um só) e API_VERSION=v25.0. `INSTAGRAM_DM_FORMATO`
  **não** foi criada (é o interruptor de emergência). Redeploy dos dois recursos.
- **Handshake do webhook validado em produção**: token errado → 403; token
  real → devolve o `hub.challenge` em texto puro. (Antes das envs: 503.)
- **Frontend SEM merge develop→main** — o código já estava em `main` desde o
  acidente de 22/08. Dois commits cirúrgicos e divergentes de propósito:
  `main 2d336a8` (remove o gate das 4 rotas, item volta ao menu — o cadeado
  por plano MAX continua —, aba em Configurações, + fix mobile da barra do
  editor) e `develop 06a396d` (gate compartilhado **separado**: Instagram sai,
  Grupos/Ofertas/Templates continuam hml-only).
- **Validação Playwright em produção** (desktop 1440 + mobile 390, logado):
  menu mostra "Automação Instagram" com cadeado (conta de teste não é MAX),
  `/dashboard/automacoes` redireciona para `/dashboard/planos` (gate de plano
  correto), `?tab=instagram` abre o card, e `/dashboard/grupos|ofertas|templates`
  continuam bloqueadas. **Zero respostas 5xx.**

### No painel da Meta (feito pelo dono do app)

Redirect URIs adicionadas (hml + `marketdash.com.br` + `www.marketdash.com.br` —
o redirect_uri sai de `window.location.origin` e a Meta exige match literal,
então o www é obrigatório), deauthorize e data-deletion apontando para
`api.marketdash.com.br`.

### Pendências desta rodada

- ✅ **Swap do webhook FEITO** (manhã de 02/09): callback agora é
  `api.marketdash.com.br/webhooks/instagram`, handshake verificou verde,
  `comments` seguiu assinado em v25.0 (não resetou). Botão Test confirmado
  ponta a ponta no log do worker (12:03 UTC: task recebida, payload de
  exemplo `17865799348089039`, descartado com `conta não conectada` — o
  correto para ids fake). **hml não recebe mais comentário real** — testar
  lá com `scripts/simular_comentario_instagram.py`.
- ✅ **E2E real em produção FEITO** (09:23 BRT de 02/09): `joaoivsonn`
  conectado do zero (`webhook_subscrito=true`, 3 escopos), automação em post
  real, comentário de `orquestraia` → `reply_status=enviado` +
  `dm_status=enviado`, DM com a mensagem completa entregue. Bônus: a conta
  `promosdabeatrizz_` (user 9) também já conectou.
- D+1: conferir 1ª execução do cron em `cron.job_run_details`.
- Assets `public/instagram/passo-{1,2,3}*.png` continuam pendentes de design
  (degradação silenciosa, não quebra).

## [Não versionado] - 2026-09-01 (Escala: o servidor WAHA vira pool, e o worker deixa de ter 4 slots)

Rodada de infraestrutura para o degrau de escala — nada disso muda tela, e tudo
muda quanto o sistema aguenta.

### Specs do VPS: o doc mentia há 7 meses

`CONFIGURACAO-COMPLETA-INFRAESTRUTURA.md` (25/01) dizia **KVM 2 — 2 vCPU / 2 GB**;
`PLANO_ESCALA_100_USUARIAS.md` (27/08) dizia **KVM 4 — 4 vCPU / 16 GB**. Medido
no servidor: o segundo está certo. São **15 GB, com 11 GB disponíveis** com a
stack inteira rodando.

A diferença não era detalhe. Com 2 GB, o teto de 60 sessões WAHA seria fantasia
(o real ficaria em 10–15) e comprar servidor seria urgente. Com 11 GB livres,
os tetos que existem hoje são **números escolhidos no código**, não capacidade
de hardware.

Continua **não medido**: RAM por sessão WAHA. Medir exige parear vários chips
reais de uma vez, o que não é viável agora — a saída adotada é o teto por
servidor virar configurável (ver abaixo) e subir conforme sessões reais entrem.

### O worker tinha 4 slots, e era esse o teto do produto

O worker subia sem `--concurrency`, então o Celery usava o nº de vCPUs: **4**.
Esse default existe para trabalho de CPU. As tasks de envio em grupo fazem o
oposto — **dormem** entre mensagens (pausa 8–20 s por rodada) e seguram o slot
por até 15 min (`WHATSAPP_FATIA_ORCAMENTO_S`). Resultado: no máximo 4 afiliadas
enviando por vez, com CSV e Shopee disputando os mesmos 4 slots. Era o gargalo
real, muito antes de qualquer limite de sessões.

Subir a concorrência sozinha estouraria o Supabase: o pool é **por processo**
(5+5), então 8 processos dariam 80 conexões contra as 40 de hoje. Por isso
`DB_POOL_SIZE`/`DB_MAX_OVERFLOW` viraram settings e o worker roda com 2+3 —
**8 × 5 = 40, exatamente o total atual**. Dobra a vazão sem pedir nada a mais
do banco. Os defaults (5+5) preservam a API.

Ainda **não feito**: separar a fila de envio em worker dedicado. Exige criar o
serviço no Coolify **antes** do merge — task roteada para fila sem consumidor
some em silêncio, o que já aconteceu duas vezes aqui.

### `waha_servidores` (migration 071): crescer vira INSERT

Antes o servidor era UM, fixo em `settings.WAHA_URL`, e não havia para onde
apontar a sessão 61 — cada salto de capacidade era migração de infra. Agora o
endereço vem do servidor da própria sessão, e adicionar caixa é uma linha na
tabela: sem deploy, sem migration, sem tocar no motor de envio.

O desenho copia o pool de proxy da 068, que já funciona. Duas diferenças:

* a afinidade por usuária aqui é **preferência** (debug e raio de incêndio), não
  isolamento — quem isola vizinhança é o proxy, que dá o IP;
* `aceita_novas` separa **drenar** de **desligar**, porque **sessão não migra de
  servidor**: o estado do whatsmeow vive no Postgres daquela caixa. Esvaziar um
  shard é parar de alocar e esperar a rotatividade, ou re-parear com aviso.

Três armadilhas tratadas:

* **`reconciliar_orfas` varria só o servidor padrão.** Órfã não tem linha no
  banco — é a definição — então o resolvedor cairia no padrão e o DELETE iria
  para a caixa errada, em silêncio. Órfã em shard não visitado viveria para
  sempre, comendo a RAM que o pool existe para administrar. Agora varre todos os
  servidores e apaga onde encontrou.
* **O cap global virou `SUM(max_sessoes)`**, com o env como trava de segurança.
  Só que "adicionei servidor e a capacidade não mudou" seria um mistério
  silencioso: quando o env está segurando o teto, sai `WARNING` dizendo isso.
* **`criar_instancia` exigia `WAHA_URL` no env**, o que travaria um ambiente
  100% pool — o destino do desenho. Passa a aceitar as duas fontes.

Fallback é proposital em todo lugar: sessão sem servidor, tabela inexistente e
pool vazio caem em `settings.WAHA_URL`. A 071 não pode ser um degrau que quebra
ambiente antigo, nem no instante do deploy, antes do backfill.

O resolvedor tem cache em memória com TTL de 60 s — sem ele seria uma query por
mensagem enviada. É seguro porque a alocação é definitiva; o TTL existe só para
o caso de um admin editar `base_url`/`api_key`.

⚠️ **A migration tem duas metades.** Sem
`scripts/backfill_waha_servidor.py --apply`, as sessões vivas ficam com
`servidor_id` nulo: nada quebra (o fallback cobre), mas o cap global conta
errado e a alocação enxerga o pool mais vazio do que está. Rodar no mesmo dia.

### Fila dedicada do envio, e o incidente que a `create_all` causou em hml

`roteiros.processar_execucao`, replicação de captura e sonda de proxy passam a
ter **fila própria**. Motivo: a task dorme com o slot na mão por até 15 min, e na
fila única o upload de CSV da afiliada ficava atrás desse sono.

O risco era repetir pela terceira vez o bug histórico do broker — task roteada
para fila que ninguém consome é aceita, enfileirada e **nunca executa**, sem erro
nem log (foi assim com `priority=5` e com a fila derivada de `ENVIRONMENT`).
Resolvido por construção: as duas filas entram em `task_queues`, e worker sem
`-Q` consome todas as filas declaradas ali. O worker atual cobre as duas sozinho;
o dedicado é otimização, não pré-requisito. `test_celery_filas.py` trava isso.

⚠️ **Incidente durante esta rodada, e vale como aviso permanente.** O push do
model `waha_servidores` para `develop` disparou o deploy de hml, e o
`create_all` do boot **criou a tabela antes da migration** — exatamente o cenário
da seção 0 do runbook de promoção. Pior: `create_all` não altera tabela
existente, então `whatsapp_instancias.servidor_id` **não** foi criada e toda
query em `whatsapp_instancias` passou a falhar com `UndefinedColumn`. A aba
Números de homologação ficou fora do ar até a 071 ser aplicada.

Duas lições: (a) a corrida `create_all` × migration **não é teórica e não espera
o merge para produção** — ela acontece no push para `develop`; (b) migration com
`ALTER TABLE` é a metade perigosa, porque a tabela "existir" esconde que a coluna
não existe. A 071 é idempotente e a aplicação a consertou sem perda de dado.

### Worker dedicado no ar em homologação — e o bug que ele denunciou

`celery-whatsapp-hml` criado no Coolify (uuid `cogwsgwocwk8k4wkswokks0s`), com as
38 variáveis do worker atual copiadas uma a uma e mais `CELERY_SOMENTE_WHATSAPP=true`.
Os dois workers rodam **a mesma imagem**; um env decide o papel, via
`scripts/worker-entrypoint.sh`. Dois Dockerfiles seria como o dedicado fica com
código velho quando alguém mexe no outro.

🔴 **O banner de boot do worker novo revelou um bug sério que eu mesmo tinha
introduzido horas antes.** `Queue(nome)` sem `exchange`/`routing_key` herda o
default (que vem de `task_default_queue`), então as duas filas ficaram no MESMO
exchange `direct` com a MESMA key — e exchange direct entrega a **todas** as
filas que casam com a key. `roteiros.processar_execucao` era publicada uma vez e
caía nas **duas** listas.

E não precisava de dois workers para doer: o worker padrão consome as duas
filas, então **ele sozinho** pegava a mensagem duas vezes e executava a fatia
duas vezes. Em envio de grupo isso é **mensagem duplicada para a afiliada** — o
caminho mais curto para o número ser banido, justamente o que o ritmo anti-ban
inteiro existe para evitar.

Corrigido com exchange e routing_key explícitos por fila. **Impacto real: zero** —
homologação tem 0 execuções e 0 mensagens em toda a história da tabela, então a
duplicação nunca chegou a disparar.

A lição que ficou em teste: nenhuma asserção sobre `task_routes` pegava isso,
porque o roteamento estava certo — errada era a *ligação* da fila.
`test_task_de_envio_e_entregue_a_UMA_fila_so` publica numa transport de memória
e conta em quantas filas a mensagem cai; verifiquei que ele falha no código
anterior. Config que "parece certa" precisa de teste de comportamento, não de
teste de config.

O passo de deploy do worker novo entrou no `deploy-homologation.yml` e **falha
alto** como os outros dois — worker de envio com código velho manda mensagem com
a lógica errada, o que é pior que um deploy vermelho.

**Servidor `busy` removido do Coolify.** O registro tinha o IP gravado como
`http:31.97.22.173` (com o esquema colado) e estava inalcançável — o primeiro
deploy do worker novo caiu nele com `ssh: Could not resolve hostname
http:31.97.22.173`. Conferido antes de apagar: 0 recursos nele, e os 8 apps mais
os 2 Redis todos no `localhost`, que é o servidor real (o Coolify roda no próprio
VPS). Registro órfão que só servia para fazer alguém tropeçar.

### Isolamento total (fecha a rodada)

`CELERY_PAPEL` ganhou três valores — `comum`, `whatsapp`, `todas` — e os dois
workers de homologação foram separados: o comum só na fila geral, o dedicado só
na de envio. Rajada de envio não ocupa mais slot de CSV, e upload pesado não
atrasa mais envio.

⚠️ **A rede acabou.** Enquanto o comum consumia as duas filas, o worker dedicado
era otimização: se caísse, os envios saíam mais devagar mas saíam. Agora, worker
de WhatsApp fora do ar = envio enfileirado até alguém perceber. `todas` segue
como default do entrypoint justamente para que container sem a variável nunca
deixe fila parada.

🔴 **Pendência que bloqueia levar o isolamento para produção: não existe alerta.**
E o alerta óbvio não serve — **`status: running` do Coolify não significa
"consumindo"**: container de pé pode estar travado sem puxar da fila, e é
justamente esse o caso que dói. O sinal honesto é **profundidade da fila no
Redis** (`LLEN marketdash-<ref>-whatsapp` crescendo sem drenar) e/ou ausência de
`sync with` no log dos workers. As notificações do Coolify (Discord/Telegram/
e-mail) são configuração de UI — a API v1 não expõe esses campos, então não dá
para automatizar daqui. Registrado em `.claude/memoria/DECISOES.md`.

Em homologação o risco é aceitável (0 execuções na história da tabela). Em
produção não: envio parado em silêncio é indistinguível de "não havia o que
enviar".

### Capacidade, antes e depois

| | Antes | Agora |
|---|---|---|
| Envios simultâneos | 4 | 8 |
| Sessões | 60 (constante no código) | soma do pool, com o env travando |
| Adicionar servidor | migração de infra | `INSERT` |

O teto que sobra é **configuração, não hardware**:
`WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA=5000` amarra em ~20 afiliadas MAX, e a caixa
de 11 GB livres quase certamente aguenta mais.

## [Não versionado] - 2026-08-31 (Dispositivos: um card por número, com os grupos dentro)

A aba **Configurações › Dispositivos › Números** era uma lista crua de números
e, embaixo dela, uma tabela com os grupos de *todos* os números juntos. Para
responder "o que este chip aqui está fazendo?", a afiliada tinha que cruzar as
duas listas com o dedo.

### Added

- **Cada número virou um bloco expansível com os próprios grupos dentro.**
  Cabeçalho com ponto de status, identidade (nome + número mascarado),
  contagem de grupos e desde quando está conectado; rodapé com a ação
  contextual. Ações secundárias saíram do rosto do card para o menu `⋮` e o
  modal **Gerenciar**, com o **Remover** isolado no canto oposto ao botão que
  ela mais aperta.
- **Renomear o número** (`PATCH /api/v1/whatsapp/instancias/{id}`). Antes o
  nome era escolhido uma vez, na criação, e nunca mais mudava. É `UPDATE` numa
  coluna cosmética: `nome_instancia` — a chave que roteia o webhook do WAHA —
  continua imutável.
- **Pausar o envio por um número, sem removê-lo** (migration `070`, colunas
  `envio_pausado` e `pausado_em`). Até aqui, a única forma de parar de disparar
  por um chip era deletá-lo e reparear tudo do zero.
- **Bucket "Grupos sem dispositivo ativo".** Remover um número é soft-delete e
  o vínculo histórico continua no banco — sem esse bloco, os grupos sumiriam da
  tela sem explicação.

### Fixed

- Grupo que está em dois chips aparece nos dois blocos (é o desenho: o motor
  faz failover entre eles) e agora vem marcado com **"também em: X"**. Sem
  isso, a repetição parecia bug.

### Notas de implementação

- **Pausa é um eixo separado de `status`, não um valor dele.** Quem escreve em
  `status` é o webhook do WAHA (`aplicar_evento_de_status`): uma pausa gravada
  ali seria apagada no próximo evento `WORKING` e o chip voltaria a disparar
  sozinho. Um número pode estar **conectado E pausado** — e é justamente o
  caso de uso (pausar o chip saudável que está sendo usado demais). Mesmo
  desenho do pool de proxies (`068`): `ativo` = intenção humana, `status` =
  saúde automática.
- A pausa é respeitada em `roteiro_envio_service._instancias_elegiveis` (o pool
  de envio) e em `grupo_evento_service._cliente_do_grupo` (remoção por
  blacklist é escrita ativa no WhatsApp). **Não** vale para sincronizar grupos,
  snapshot nem monitoramento — leitura e escuta continuam, pausar envio não é
  desconectar.
- ⚠️ A `070` é `ALTER TABLE`, não `CREATE TABLE`: `create_all` **não adiciona
  coluna em tabela existente**. Aplicar antes do deploy, senão
  `GET /instancias` quebra com `UndefinedColumn`. Aplicada em **hml em
  31/08/2026**; **pendente em produção** (onde a aba nem aparece —
  `isProductionHost()`).

## [Não versionado] - 2026-08-28 (O gasto do Meta sumiu da tela de 3 alunas)

Uma aluna relatou que o gasto de ontem não aparecia — a tela de Campanhas
mostrava **R$ 0,00** e, pior, **Lucro +R$ 22,67 num dia que deu prejuízo de
R$ 53,36**. O Gerenciador da Meta mostrava R$ 76,03 gastos em 27/08.

O dado **já estava dentro do MarketDash**, campanha a campanha, centavo a
centavo — só que na tabela errada. O sync faz duas chamadas à Graph API:

| Chamada | Volume | Grava em | Estado |
|---|---|---|---|
| `{campaign_id}/insights` | **uma por campanha** | `campaign_daily_insights` (o que a tela lê) | parou em 26/08 16:20 |
| `act_X/insights` + `breakdowns` | uma por conta | `campaign_platform_daily_insights` | funcionando |

A primeira passou a responder **HTTP 200 com `data: []`** para todas as
campanhas de 3 contas, de forma permanente. Sem exceção, sem log de erro: o
`except HTTPException` seguia com `insights = []` e o upsert de lista vazia é
um no-op. O `max(updated_at)` das linhas dessas alunas ficou congelado no
minuto exato em que parou.

**O que provavelmente causou.** Com 70+ campanhas por conta e o cron de hora em
hora, o sync disparava **~2.200 chamadas por hora** à Graph API (uma por
campanha de cada usuária, 24h por dia). As 3 contas atingidas são as de menor
gasto — e o limite da Meta escala com o gasto da conta. Não foi possível
confirmar o erro exato: reproduzir a chamada exige o token da aluna. O que os
dados **descartam**: não é deploy (3 de 38 contas, em dois momentos distintos) e
não é o token (a outra chamada usa o mesmo e passa).

**A correção.** Os insights passam a vir em **uma chamada por conta**
(`act_X/insights?level=campaign`, sem breakdown) — a mesma que nunca parou de
responder. De ~2.200 chamadas por ciclo para ~40. O custo em rate limit deixa de
crescer com o número de campanhas da aluna. `get_campaign_insights` foi removido
para não voltar por descuido.

**Por que ninguém viu por dois dias.** O painel `/admin/sincronizacoes` marcava
`success` e **72/72** em todos os 24 ciclos diários. O contador somava CAMPANHAS
LISTADAS, não insights gravados. Agora `records_upserted` conta linhas de insight
de fato gravadas, e o run é marcado **"Possível parcial"** (`is_suspected_partial`,
que o painel já exibia) quando nenhum insight entra **enquanto o placement traz
linhas** — a assinatura exata da falha. Conta que só parou de anunciar não
dispara o alerta: aí as duas chamadas vêm vazias.

**Dados corrigidos.** 33 linhas de insight de 26–28/08 reconstruídas a partir do
placement (a soma dos placements de uma campanha num dia É o insight daquele
dia — conferido contra o Gerenciador da Meta e contra 8 dias de dados de todas
as usuárias, onde as duas fontes batem exatamente), e `ad_spends` reprojetado.
R$ 206,30 de gasto que não aparecia: Alice R$ 142,44, Mariana R$ 48,36,
Katyusci R$ 15,50.

**Fica pendente:** a cadência de hora em hora do cron do Facebook. 24 ciclos por
dia para um dado que muda pouco depois de 3 dias é o que colocou o volume nesse
patamar.

### Sincronizações presas em "running" desde julho

Apareceu ao validar o deploy acima: o ciclo das 16:00 processou **25 alunas em
vez de 38** e deixou um run em `running`. É o rastro do container sendo trocado
no meio — o ciclo roda como `BackgroundTask` do processo da API e morre junto
com ele.

Puxando o fio, havia **50 runs presos em `running`**, o mais antigo de **28/07**
(31 dias). Nada nunca os fechava. Todos contavam como "rodando agora" no painel,
e a maioria era das duas contas com mais campanhas (296 e 341) — as que mais
demoravam e mais chance tinham de ser pegas por um restart.

`fechar_orfaos()` roda no início de cada ciclo e encerra o que passou de 1h em
`running`, de qualquer origem. O limiar não é novo: é o mesmo
`STALE_RUNNING_SECONDS` que o painel já usava para marcar "(travada?)" — agora
numa constante só, no repository. Duas cópias sairiam de sincronia e o painel
acusaria numa faixa enquanto a limpeza fecharia noutra. Folga real: o run mais
longo que **terminou** em 30 dias levou 7,9 min.

Status próprio **`interrupted`** ("Interrompida", cinza no painel), não
`failed`: nada falhou na API, o processo foi morto. Marcar como falha inflaria
`errors_24h` e a aba de erros com 50 registros que não pedem ação nenhuma.

⚠️ **Todo deploy trunca o ciclo em andamento.** Hoje é inofensivo — a janela do
sync é de 3 dias e o ciclo seguinte cobre —, mas explica runs "sumidos" no
painel. Mover o ciclo para o worker do Celery resolveria de vez; não foi feito
porque ele foi movido para inline de propósito (Rodada 5), quando o worker
rodava com código velho.

## [Não versionado] - 2026-08-27 (Layout no celular e no tablet)

O painel estava quebrado no celular e no tablet. Auditoria por screenshot das 25
telas (área da aluna e painel admin) em três tamanhos — celular, tablet e
desktop — e correção do que apareceu. Elementos passando da borda da tela:
**celular 51 → 14, tablet 32 → 0, desktop segue em 0.** Os que restam são a
barra de abas do admin, que rola de propósito, e dois elementos decorativos.

**Ofertas** era a pior: dois cards por linha no celular quando o card foi
desenhado para ocupar a linha inteira, então nome, preço e comissão apareciam
cortados. Agora é um card por linha no celular e dois no tablet.

**Editor de automação**: os botões "Salvar rascunho" e "Publicar automação"
ficavam atrás da barra de navegação inferior — publicar era impossível pelo
celular.

**Painel admin** não tinha nenhum tratamento para telas pequenas. A tabela de
uso da plataforma era cortada sem oferecer rolagem, os filtros não cabiam na
largura da tela e os rótulos longos do DRE empurravam os valores para fora.
Tabelas largas agora viram cards no celular e no tablet, e a aba aberta aparece
sozinha na barra de abas.

**Modais** deixaram de encostar nas bordas no celular, e os que ainda abriam
como caixa centralizada passaram a abrir como gaveta — incluindo o de nova
despesa. No envio rápido de oferta, o texto e o link saíam da tela.

**Meus Links** ficava com o título vazio no topo e desperdiçava 56px dos 390px
da tela com margem repetida.

Para quem for validar: `mobile-audit.mjs` agora percorre as rotas atuais nos
três tamanhos e reporta erro de console junto com o screenshot.

## [Não versionado] - 2026-08-26 (O sync de grupos: 499 viravam zero)

A sincronização de grupos vinha terminando "com sucesso" e gravando nada. O log
de homologação deu a causa exata: `100 de 100 itens sem JID reconhecível (chaves
do 1º: ['AddressingMode', 'AnnounceVersionID', 'CreatorCountryCode', ...])`.

São os campos de `types.GroupInfo` do **whatsmeow**. O engine **GOWS serializa a
struct Go como ela é** — `JID`, `Name`, `Participants`, `IsAdmin`, `IsAnnounce`,
com as structs embutidas achatadas — enquanto NOWEB e WEBJS usam `id`, `subject`,
`participants`, `role`, `announce`. A documentação do WAHA diz apenas "a resposta
depende do engine", sem mostrar a diferença. Nosso parser lia só `id` minúsculo,
então descartava **todos** os grupos: 5 páginas, 499 grupos, zero gravado.

O que mudou:

- **Leitura tolerante à caixa** (`waha_client.campo`/`tem_campo`), no sync e no
  webhook de participantes. `types.JID` implementa `MarshalText`, então o JID
  chega como string simples — não como objeto.
- **Identidade própria via LID.** Em grupo com endereçamento LID o participante
  vem como `…@lid` e o telefone fica em `PhoneNumber`; comparar só o telefone
  fazia o número não se reconhecer e todo grupo nascia "não sou admin", travando
  envio e convite.
- **Convites saíram de dentro do laço.** É uma chamada HTTP por grupo: com 499
  grupos o request estourava o tempo, o proxy cortava a conexão (o "Failed to
  fetch" da tela) e, como o commit só vinha no fim, o sync inteiro se perdia.
  Agora grava os grupos primeiro e busca convites dentro de um orçamento de
  tempo; o resto entra no próximo sync.

E a lição virou código: página com itens e **nenhum** reconhecido agora sobe
erro em vez de terminar `success` com `vistos=0`. Foi o sucesso silencioso que
escondeu isto — a tela dizia "nenhum grupo ainda" e não havia como distinguir
"a conta não tem grupos" de "vieram 499 e não entendemos nenhum". `ignorados`
passou a viajar na resposta e a aparecer na tela.


### Desdobramento: o convite chegava e era jogado fora

Com os 499 grupos entrando, o sync real mostrou **169 grupos de admin e zero
links de convite**. A falha era silenciosa — `convite_do_grupo` devolvia `None`
em qualquer 4xx e quem chamava engolia a exceção, o mesmo padrão que tinha
acabado de custar dias. Instrumentado, o motivo apareceu na primeira execução,
gravado em `sync_runs.details`:

```
convite: resposta sem código: {'texto': 'https://chat.whatsapp.com/FG7Oij…'}
```

O WAHA devolve o **link inteiro em texto puro**, não JSON — e por isso caía no
invólucro `{"texto": …}` que o `_pedir` usa para corpo não-JSON. `dados.get("code")`
dava `None`. O link estava chegando o tempo todo.

E o teste dessa correção descobriu algo pior: **403 no `invite-code` era
classificado como `auth`, que é motivo FATAL**. Um único grupo onde não somos
admin — ou que simplesmente não permite convite — marcaria o **número inteiro**
como desconectado, e o sync passa por centenas de grupos de uma vez.
`renomear_grupo` já tratava isso com `auth_em_403=False`; o convite tinha ficado
de fora. Com 169 grupos de admin, era questão de tempo.

O diagnóstico agora fica em `sync_runs.details` e é auditável em
`/admin/sincronizacoes`, sem precisar de acesso ao log do container.

## [Não versionado] - 2026-08-26 (Documentação de promoção + o proxy que apontava para produção)

Documento único de promoção para produção — `docs/PROMOCAO_PARA_PRODUCAO.md` —
cobrindo Grupos de WhatsApp e Instagram, com o estado dos dois bancos **medido**,
não lembrado.

A medição desmentiu o que os checklists assumiam sobre o Instagram: as três
tabelas **já existem em produção**, criadas pelo `create_all` do boot e não pela
migration `052`. As colunas da `054`/`055`/`056` estão lá porque vêm dos models —
então conferir "a coluna existe?" dá falso positivo. **As policies da `052` e o
cron da `053` nunca foram aplicados em produção.** Não é vazamento (RLS ligado sem
policy nega tudo para `anon`, e a API conecta com `BYPASSRLS`), mas a segunda
linha de defesa não existe lá, e o cron ausente vira problema real no lançamento:
o token do Business Login dura 60 dias e só renova enquanto está válido.

Dois achados que valem por si:

- **As rotas do Instagram e as dos Grupos dividem o mesmo bloco
  `!isProductionHost()`** em `app-routes.tsx`. Remover esse gate para liberar
  Grupos liberaria o Instagram junto, sem App Review. O bloco precisa ser
  separado na promoção.
- **`nginx.conf` mandava `/g/` e `/conectar/` para `api.marketdash.com.br` fixo**
  — em homologação, isso servia essas páginas a partir da **API de produção**
  (que nem tem as tabelas). Corrigido com `map $host $api_upstream`, espelhando o
  `API_BY_HOST` do frontend. Como `proxy_pass` com variável resolve DNS em
  runtime, foi preciso um `resolver`: usamos DNS público, não o `127.0.0.11` do
  Docker — verificado que o DNS embutido **recusa conexão na bridge padrão**, o
  que derrubaria o `/g` inteiro por causa da topologia da rede.

O runbook antigo (`PROMOCAO_MODULO_GRUPOS.md`) foi escrito antes da migration
`067` e não a listava; agora lista, e aponta para o documento mestre.

## [Não versionado] - 2026-08-26 (Migração das chaves do Supabase)

O Supabase trocou o formato das chaves de API: `anon`/`service_role` deram lugar
a `sb_publishable_…`/`sb_secret_…`. O código passa a aceitar **as duas formas**,
preferindo a nova — o que permite rotacionar sem janela de indisponibilidade:
acrescenta a nova, faz o deploy, confirma, remove a antiga.

Vale para o backend (`SUPABASE_PUBLISHABLE_KEY`/`SUPABASE_SECRET_KEY`) e para o
frontend (`VITE_SUPABASE_PUBLISHABLE_KEY`). No frontend isso importa mais do que
parece: a checagem de configuração **lança** antes de qualquer render, então
configurar só o nome novo dava tela branca.

### O que NÃO precisou mudar, e por quê

A validação de token é `auth.get_user(token)` — uma chamada ao servidor do
Supabase, sem decodificação local de JWT. Verificado contra o projeto real: as
duas chaves autenticam e um token obtido com uma é aceito pelo client criado com
a outra. Por isso a mudança do algoritmo dos tokens para **ES256** não exigiu
nada do nosso lado. `SUPABASE_JWKS_URL` fica declarada, mas sem uso — só faria
falta se algum dia validássemos o JWT localmente.

### Fixed

- Nenhum ponto do código lê mais `SUPABASE_KEY`/`SUPABASE_SERVICE_KEY` direto:
  o acesso passa por `supabase_chave_publica`/`supabase_chave_admin`. Um ponto
  esquecido quebraria só em ambiente já rotacionado, e quebraria como **401** —
  o erro mais caro de diagnosticar. Há teste varrendo o código-fonte por isso.

## [Não versionado] - 2026-08-26 (Grupos: itens 17 e 18 da spec + o sync que trazia zero grupos)

Fecha os dois itens que faltavam do módulo e conserta o bug que os testes de
homologação expuseram: dispositivo conectado, sincronização "bem-sucedida" e
nenhum grupo.

### Fixed — o sync trazia zero grupos e dizia que deu certo

- **Formato de resposta desconhecido virava lista vazia, em silêncio.** A
  documentação do WAHA diz que a resposta de `/groups` **depende do engine**, e
  o código fazia `dados if isinstance(dados, list) else []`: qualquer envelope
  diferente resultava em zero grupos com o sync marcado como SUCESSO. Foi
  exatamente o que aconteceu — quatro sincronizações seguidas com `vistos=0` e
  nenhum log dizendo por quê. Agora envelope conhecido é desembrulhado e
  formato irreconhecível vira **erro**: zero grupos precisa ser um fato do
  WhatsApp, nunca um formato que não soubemos ler.
- **`id` de grupo em forma de objeto descartava tudo.** O identificador vem como
  texto em alguns engines e como objeto em outros; o filtro comparava
  `str(objeto)` com `@g.us` e nunca casava. Passa a aceitar as duas formas, e
  uma página com itens e nenhum grupo reconhecido agora **registra um aviso**
  com as chaves recebidas, para o próximo caso ser diagnosticável.

### Fixed — a tela dizia para conectar um dispositivo já conectado

Campanhas e a lista de grupos tratavam "sem dispositivo" e "dispositivo
conectado, mas sem grupos" como o mesmo estado. Com a sincronização vazia, a
afiliada era mandada conectar um número que ela acabara de conectar — e a ação
que resolvia (sincronizar) ficava escondida. Os dois estados agora têm texto e
botão próprios.

### Added

- **Blacklist de números (item 17).** Nova seção em Configurações. Bloqueia o
  resumo diário e, quando o número entra num grupo, remove — só onde a afiliada
  é administradora. O número **não é guardado em claro**: vai como HMAC
  irreversível e a lista mostra `+55 11 ****-4321`, com a explicação na tela.
  "Não quero que receba" e "quero fora dos meus grupos" são escolhas separadas,
  por entrada. A tabela existia desde a migration 060 e estava inerte.
- **Link de conexão externa (item 18).** A afiliada gera um link temporário para
  quem está com o celular escanear o QR, sem acesso à conta dela. A tela é
  pública, então o token é tratado como senha: 32 bytes aleatórios, só o hash no
  banco, 15 minutos, **morre ao conectar** (não no fim do prazo) e gerar outro
  invalida o anterior. A página não revela nada além do QR, e link inválido,
  expirado, usado ou revogado mostram o mesmo texto.

### Changed

- "WhatsApp" virou **"Dispositivos"** na navegação das Configurações.
- A lista de grupos passa a mostrar **qual dispositivo** cada grupo pertence, em
  vez da contagem — "1" não responde "esse grupo está em qual dos meus números?".
- **Ofertas abre nos mais vendidos, sem precisar buscar.** Medido contra a API
  real: `productOfferV2` com termo vazio devolve a vitrine da conta — o
  comentário no código dizia o contrário, e era o que justificava a tela abrir
  vazia. E o `sortType: 2` da Shopee é RANKING, não ordenação ("fone" devolveu
  17253, 11876, 5440, 12209…), então "mais vendidos" só é verdade porque
  passamos a ordenar de fato.
- Grade de Ofertas sobe **de uma em uma**: 2 → 3 → 4 (1100px) → 5 (1366) → 6
  (1600).
- **Variável de ambiente desconhecida passa a ser ignorada, não recusada.** O
  padrão do pydantic derrubava o app inteiro no boot: uma migração das chaves do
  Supabase acrescentou quatro variáveis ao `.env` e a API parou de subir, junto
  com a suíte de testes. Em produção o efeito seria pior — qualquer env
  acrescentada no Coolify viraria crash-loop.

## [Não versionado] - 2026-08-26 (Auditoria final do Módulo de Grupos — F0 a F8)

Antes de dar o módulo por concluído, seis auditorias independentes varreram os
dois repos (contrato entre backend e frontend, privacidade, dinheiro, isolamento
por usuário, operação e a UI nova), cada uma seguida de uma tentativa de
**refutar** os próprios achados. O que sobreviveu está abaixo. Nada disso
quebrava teste; quase tudo só apareceu rodando de verdade.

### Fixed — privacidade

- **O "código irreversível" da política era reversível em minutos.** De quem
  entra ou sai dos grupos guardamos um código derivado do número — e o código
  fazia `sha256(número + salt)` seguindo em frente **sem salt**, que era o caso
  em todos os ambientes (a env é opcional e nunca esteve definida). Telefone tem
  espaço de busca minúsculo: medido em Python puro, 1,5 milhão de hashes por
  segundo, número de teste recuperado em 0,4 s, e o espaço inteiro de celulares
  brasileiros varrido em ~11 minutos. Quem obtivesse o banco teria a lista de
  telefones. Agora é `HMAC-SHA256` com segredo garantido — sem a env, derivado
  de um segredo que o app já exige. O mesmo ataque não recupera mais nada.
- **Desligar o monitoramento dizia "pronto" sem ter desligado.** Quando o
  WhatsApp não respondia, o backend tratava como "nada a fazer" e devolvia
  sucesso — com a conexão possivelmente ainda recebendo as mensagens do grupo.
  Agora o estado "não deu para saber" é explícito: desligar exige confirmação e,
  sem ela, avisa que o monitoramento continua ligado.
- **Retenção**: o texto dizia que as capturas somem em 30 dias sem mencionar que
  o que ela escolheu enviar continua no histórico de envios dela. Corrigido.

### Fixed — dinheiro

- **O bloco do Dashboard dobrava tudo** quando um grupo estava em duas
  campanhas (o que o produto permite de propósito): 1 grupo com 100 pessoas e
  R$ 100 de comissão aparecia como 200 pessoas e R$ 200.
- **Gasto de campanha sem grupos entrava no "Investimento" e sumia do "Lucro"**.
- **"Lucro por pessoa" sem participante mostrava R$ 0,00** — que afirma "cada
  pessoa rende zero", diferente de "a métrica não existe". Agora mostra "—".

### Fixed — isolamento

- **O template de outra afiliada podia ser enviado nos seus grupos.** O
  `template_id` do passo do roteiro vinha do cliente e ninguém checava dono —
  nem ao salvar, nem no disparo. Fechado nos dois pontos.

### Fixed — interface

- **Grupo sem nome derrubava a página inteira.** O nome é opcional no backend
  mas o tipo do frontend dizia que não, então nada acusava. Seis pontos liam o
  valor direto (cinco buscas e uma ordenação) e, sem barreira de erro no
  projeto, a tela da campanha inteira ficava em branco — não só o componente.
- **Trocar de monitoramento rápido mostrava as ofertas do anterior** sob o nome
  do novo, sem nada indicando o erro.
- **A lista de capturas cortava em 50 em silêncio** enquanto o card anunciava o
  total.

## [Não versionado] - 2026-08-26 (Grupos WhatsApp F8 — Monitoramento de grupos)

Última fase do módulo. A afiliada acompanha um grupo — dela ou de terceiro, desde
que o número dela seja membro — e replica as ofertas que aparecem lá para os
grupos dela, **com o link trocado pelo dela**.

### Added

- **Monitoramento por grupo** (migration 066), desligado por padrão, com filtro
  (só mensagens com link; opcionalmente por palavra-chave), destino (uma campanha
  ou grupos escolhidos) e revisão antes de enviar. Replicar automaticamente é
  opt-in: mandar para os grupos dela um texto que outra pessoa escreveu, sem
  ninguém ler, é o pior default possível.
- **Conversão de link**: cada link da mensagem vira o link de afiliada dela antes
  do envio. Se sobrar algum que não conseguimos converter, a captura para com o
  motivo na tela em vez de sair — replicar o link do concorrente seria fazer
  propaganda para ele dentro dos grupos dela.
- **Deduplicação por conteúdo**: o dono do grupo repostar a mesma oferta de hora
  em hora não vira envio repetido.
- **Limite do plano MAX: 3 monitoramentos.** O teto é de RAM e de privacidade,
  não comercial.
- **Aba Monitoramento** dentro da campanha (sem item de menu novo — o
  monitoramento é sempre "o que alimenta ESTA campanha"). Cada captura mostra o
  **antes → depois do link**: o do concorrente riscado, o dela em destaque. É o
  que prova para a afiliada que o que saiu foi o link certo — e é exatamente
  onde o bug crítico morava.

### Privacidade — o que muda, e o que não muda

O evento de mensagem do WhatsApp é entregue **por número, não por grupo**. Com um
monitoramento ligado, as mensagens daquele número trafegam até o nosso servidor,
que **descarta antes de gravar** tudo que não é do grupo monitorado (conversa
privada e outros grupos saem no primeiro `if`) e tudo que não passa no filtro.
Sem monitoramento ativo, a sessão **sequer assina** o evento — validado contra o
WAHA real: a sessão nasce sem ele, ligar o monitoramento adiciona, desligar
remove.

Nada identifica quem escreveu: não guardamos número, nome nem hash de autor —
só o texto e um hash do próprio texto, para deduplicar. As capturas são
**apagadas automaticamente após 30 dias**: a finalidade é replicar uma oferta,
que é passageira, e guardar além disso só ampliaria o que temos de terceiro.
A política de privacidade foi reescrita: ela dizia "não armazenamos o conteúdo
das mensagens", e o monitoramento torna isso uma exceção que precisa estar
declarada.

### Fixed — encontrado na revisão adversarial, antes do commit

- **A afiliada divulgaria o link do CONCORRENTE, com status "replicada".** O
  link era salvo normalizado (`https://` na frente) mas a troca rodava contra o
  texto cru: quando o dono do grupo colava sem esquema — que é metade dos casos —
  a substituição não casava com nada e passava em silêncio.
- **Só o primeiro link era convertido.** Mensagem com dois produtos mandava o
  segundo intacto.
- **Link prefixo de outro corrompia a URL**: com `/AbC` e `/AbCdEf` no mesmo
  texto, o segundo saía pela metade.
- **Backtracking exponencial no reconhecimento de URL** — 89 caracteres
  levavam 1,1s e 120 levariam minutos. O texto vem de um grupo de terceiro:
  qualquer membro poderia travar a API. Agora é linear (0,008ms no mesmo caso).
- **Ligar um monitoramento podia apagar o webhook inteiro da sessão** quando a
  URL não estava configurada por env — o número cairia e a tela continuaria
  dizendo "conectado".
- **O "desfazer" do toggle invertia em vez de restaurar**, e conseguia LIGAR um
  monitoramento desligado — que no dia seguinte começaria a capturar sem
  ninguém ter pedido.
- **Captura presa para sempre** se o worker morresse no meio (nem replicada,
  nem em erro); e **captura em erro era irrecuperável**, porque o repost caía na
  deduplicação. Uma indisponibilidade passageira da Shopee matava a oferta.
- **Campo enviado como `null` no PATCH** derrubava a rota com 500.
- **Texto de terceiro podia vazar para o log** da aplicação pelo traceback do
  SQLAlchemy, que embute o SQL com os parâmetros.
- Model e migration geravam esquemas diferentes (defaults e índices, incluindo o
  índice parcial do caminho quente do webhook) — agora produzem DDL idêntico.

## [Não versionado] - 2026-08-26 (Grupos WhatsApp F7 — Anúncios × Grupos e Resultados)

Fecha o ciclo em números: o que foi gasto no anúncio, quem entrou, quanto o
grupo devolveu em comissão e — a métrica que decide o investimento — **quanto
cada pessoa dentro do grupo vale**.

### Added

- **Vínculo Anúncios × Campanha de grupos** (migration 065): a afiliada
  seleciona manualmente quais campanhas do Meta levam àqueles grupos. Seleção
  manual e não por conta de anúncio de propósito — vincular a conta inteira
  misturaria anúncio de lead com anúncio de venda direta e produziria um
  "custo por lead" que não é o custo de nada.
- **Leads e CPL do Meta**: campo `actions` na chamada de insights que já
  existia (sem request extra) → `campaign_daily_insights.leads`. A coluna é
  **NULL-able de propósito**: NULL é "configure o pixel" e a tela diz isso; 0
  seria a afirmação diferente de que ninguém virou lead.
- **Aba Resultados**: uma linha por grupo com participantes, entradas, saídas,
  evasão, mensagens, cliques, pedidos, comissão líquida, gasto rateado, lucro
  e **lucro por pessoa** em destaque. Máximo de 6 colunas visíveis; o resto
  atrás de "ver detalhes", DataCard no celular.
- **Gasto rateado por entradas do período**: sem isso o "lucro por grupo"
  seria comissão pura e a decisão de investimento ignoraria o que foi pago
  para encher o grupo. Sem entrada registrada, rateio igualitário.
- **Exportação de entradas em CSV** — data, grupo, origem e se a pessoa
  continua no grupo. **Nunca número de telefone**: com `getParticipants=false`
  nós sequer coletamos os números, e o identificador guardado é um hash
  irreversível.
- **Selo e filtros na tela de Anúncios**: campanha vinculada mostra a qual
  campanha de grupos pertence e leva direto aos Resultados; filtro
  "Vinculadas a grupo" / "Sem vínculo com grupo".
- **Bloco consolidado no Dashboard** (`GET /campanhas-grupos/resumo`): totais
  das campanhas ativas, sem um segundo filtro de data e sem aparecer para quem
  não usa grupos.

### Fixed — encontrado na revisão adversarial, antes do commit

- **Comissão do grupo divergia do Dashboard em 294%.** A soma por grupo não
  aplicava a allowlist de status do KpiService (um `UNPAID` de R$500 entrava
  como comissão real) e comparava "canceled" com um L, enquanto a Shopee manda
  `cancelled` com dois. No mesmo cenário: R$670 na tela do grupo contra R$170
  no Dashboard — na tela cujo propósito é decidir quanto gastar em anúncio.
- **Leads podiam sair em dobro e cortar o CPL pela metade.** Somar `lead`,
  `offsite_conversion.fb_pixel_lead` e `onsite_conversion.lead_grouped` conta
  a mesma conversão mais de uma vez se `lead` for o agregado do Meta. Agora é
  `max()`, que nunca infla — inflar lead faz anúncio ruim parecer bom e
  empurra a afiliada a gastar mais. (Relação exata a revalidar quando uma
  campanha com pixel rodar: a conta de hml ainda não tem lead nenhum.)
- **Injeção de fórmula no CSV.** O nome do grupo é escrito por qualquer admin
  do grupo, inclusive alguém que não é a afiliada; um nome começando com `=`
  virava fórmula ativa ao abrir no Excel/Sheets.
- **Emoji no nome da campanha derrubava a exportação com 500.** Header HTTP é
  latin-1 no Starlette. O nome do arquivo passou a ser ASCII.
- **Quem saiu e voltou aparecia como "ainda no grupo" nas duas entradas** —
  inflando a permanência justamente da coorte que a exportação existe para
  medir.
- **Entradas e rateio do gasto ignoravam o filtro de período**: a tela somava
  entradas históricas ao lado de comissão de 7 dias, e o gasto de agosto ia
  para o grupo que encheu em julho.
- **`func.date()` em coluna timestamptz** truncava no fuso da sessão do
  Postgres: venda das 22h de Brasília caía no dia seguinte e o total mudava
  conforme a hora em que a tela era aberta.
- **A mesma campanha do Meta podia ser vinculada a duas campanhas de grupos**,
  atribuindo 100% do mesmo gasto às duas. Agora há UNIQUE no banco e a tela
  avisa antes do clique, com o nome de quem já tem o anúncio.
- **Data inválida virava número errado em silêncio** (caía no default de 30
  dias) em vez de 422.
- **Teste que se autoenvenenava**: o teto global da plataforma conta mensagens
  de todas as usuárias no dia, e o banco de teste compartilhado acumulava —
  passadas ~5.000 linhas, todos os testes do motor falhavam parecendo
  regressão do claim. De quebra, o teto global ganhou o teste que nunca teve.

### Revisão do frontend da F7 — corrigido antes do commit

- **As notas de CPL e "custo por permanência" mentiam quando o valor era 0**:
  diziam "configure o pixel" com o pixel funcionando e reportando zero, e "sem
  entradas registradas" com 50 entradas em que todo mundo saiu. As notas passam
  a derivar de `leads`/`entradas`/`ficaram`, nunca do quociente.
- Sem guarda de resposta obsoleta, trocar de período rápido podia mostrar os
  números de 7 dias com o chip de 14 dias aceso — numa tela que decide gasto.
- Os KPIs do topo mantinham os números do período anterior por cima do card de
  erro.
- O bloco do Dashboard sumia inteiro num 500 transitório, indistinguível de
  "você não tem campanhas".
- Faltava gating de **plano** (só havia o de host): em homologação, uma conta
  Pro disparava requests MAX-only e via filtros que não funcionavam.
- Mensagem crua do backend (HTML de proxy, stack) renderizada como erro. No
  caminho, o `fetchWithAuth` chamava `.toLowerCase()` no `detail` do
  `require_plan`, que é objeto — TypeError vindo de dentro da infra.
- Status e vínculo colapsados num dropdown só: escolher vínculo apagava o
  status em silêncio, e o export ignorava o vínculo — o arquivo não batia com a
  tela. O backend passou a aceitar `vinculo` no export.
- Download do CSV revogava o object URL no mesmo tick (quebra no iOS Safari).
- Estado vazio sem a ação que resolve; botão de exportar clicável no vazio.
- `num`/`pct` estouravam em `undefined` e, sem ErrorBoundary no projeto,
  derrubariam o Dashboard inteiro.
- Abas da campanha viraram controladas pela URL — `?tab=` não navegava.

## [Não versionado] - 2026-08-31 (Como conseguir a API da Shopee)

### Added

- **Passo a passo "Como pegar sua API de Afiliada da Shopee"** na tela de
  Configurações › Marketplaces, num modal disparado por um link ao lado do
  campo AppID.

  Existe porque "não sei onde pegar isso" é onde a afiliada trava ao conectar,
  e a resposta não é óbvia: **a API não vem ligada na conta Shopee** — precisa
  ser solicitada por chamado (Central de Ajuda → E-mail, com campos
  específicos) e a ativação leva de horas a dias, sem aviso por e-mail. Sem
  essa informação, a conclusão natural dela é que o MarketDash está com defeito.

  Fica em modal, e não solto na tela, para não cobrar leitura de quem já tem a
  credencial nem empurrar o formulário para baixo da dobra no celular.

## [Não versionado] - 2026-08-31 (Proxy no ar em homologação + correções)

### Changed

- **Proxy por sessão LIGADO em homologação** (`whatsapp_proxy: true`). API,
  worker e a aba de admin estão no ar em hml; a migration **069** (cron horário
  da sonda) foi aplicada lá e o disparo manual do endpoint foi confirmado
  ponta a ponta: `202 accepted` → task no worker → `sync_runs` com
  `source="proxy_health"`, `verificados=0` (o pool está vazio — sem proxy
  cadastrado, ligar a flag não muda o caminho de nenhuma sessão).

### Fixed

- **A aba de WhatsApp do admin ainda pedia variáveis da Evolution**, removida em
  25/08: mandava configurar `EVOLUTION_URL`/`EVOLUTION_API_KEY`/
  `EVOLUTION_INSTANCIA` e consultar um `docs/whatsapp-evolution.md` que não
  existe. Quem caísse no estado "não configurado" seguiria a instrução errada
  até desistir. Agora aponta as `WAHA_*` e o doc certo.
- **Estados do WAHA apareciam crus na tela** (`scan_qr_code`, `starting`,
  `stopped`, `failed`): o mapa de rótulos só conhecia os nomes da era Evolution.

### Documentado (não resolvido)

- **Conflito de specs do VPS**: o doc de infra (25/01) diz KVM 2 — 2 vCPU / 2 GB;
  o plano de escala (27/08) diz KVM 4 — 4 vCPU / 16 GB. Isso decide se o teto de
  60 sessões WAHA é capacidade real ou fantasia — com 2 GB divididos com API,
  worker, Redis e frontend, o teto real fica perto de 10–15. O SSH da máquina
  pede senha, então o doc passou a carregar o aviso e o comando que resolve, em
  vez de duas verdades se contradizendo.

## [Não versionado] - 2026-08-27 (Proxy por sessão no WhatsApp)

Cada número conectado pode passar a sair por um **IP próprio**. Está no código,
**desligado por flag** (`whatsapp_proxy: false`) — ligar exige cadastrar os
proxies antes.

A decisão que o desenho carrega: **proxy sticky, não rotativo**. O que derruba
número no WhatsApp não é repetir o mesmo IP, é *trocar* de IP — uma sessão que
aparece em São Paulo e dez minutos depois em Frankfurt é o retrato mais óbvio de
conta automatizada. Então o que é dinâmico aqui é a **alocação** (pool no banco,
admin troca sem redeploy), nunca o IP por mensagem.

### Added

- **Pool de IPs** (`whatsapp_proxies`, migration 068) com alocação por
  **afinidade de usuária**: os 3 chips da mesma afiliada dividem um IP — é o
  retrato coerente de uma pessoa com três aparelhos em casa, e derruba o custo
  de 3 IPs por afiliada para 1. Chips de afiliadas **diferentes** nunca dividem
  IP: um banimento contaminaria a vizinhança.
- **Sonda de saúde horária** (`proxies.verificar` + `/internal/cron/proxy-health`,
  migration 069): 2 falhas seguidas → `degradado`, 4 → `quarentena` (o pool para
  de alocar nele). Registra em `sync_runs` (`source="proxy_health"`). IP diferente
  do host é normal em residencial rotativo; **país** diferente vira alerta.
- **Aba "IPs das conexões (proxy)"** no admin (Uso e Sistema › Sistema): pool com
  ocupação e status, quem está em qual IP, *Verificar* e *Realocar*. A afiliada
  não vê nada disso — para ela, pool esgotado é "sem capacidade de conexão, fale
  com o suporte", sem a palavra proxy.
- **Cooldown de 24h por chip** e contador de trocas: trocar de IP é evento raro
  e registrado, com confirmação explícita na tela.

### Fixed

- **Falha de rede deixou de ser tratada como banimento.** `timeout`/`rede` num
  chip atrás de proxy não conta mais para o disjuntor: cinco instabilidades de
  rede desconectavam o número e pediam novo QR à afiliada por causa de um IP
  fora do ar. Agora, quando *todos* os chips do mesmo IP falham na mesma fatia, o
  IP vai a `degradado` e a execução **pausa** (retomável) — e o motor alterna
  entre os chips do proxy em vez de martelar o que acabou de falhar. Chip sem
  proxy segue no comportamento antigo: sem IP dedicado não há como distinguir "o
  IP caiu" de "o WAHA caiu".
- **`desconectado`/`auth` continuam no disjuntor e NÃO trocam de IP.** Trocar de
  proxy porque o número foi banido queima o IP seguinte também.
- **O `PUT` de webhooks não apaga mais o proxy da sessão.** `webhooks` e `proxy`
  moram na mesma chave `config` do WAHA: ligar/desligar um monitoramento
  reescrevia o `config` e devolveria a sessão ao IP do servidor, em silêncio.

### Pendente

- **Não está medido se aplicar um proxy novo numa sessão já pareada
  (`stop` → `PUT` → `start`) exige novo QR.** Até o spike em homologação
  responder, a realocação automática por quarentena só mexe no banco e alerta; a
  aplicação na sessão é sempre um clique de gente, com aviso na tela
  (`WHATSAPP_PROXY_APLICAR_AUTOMATICO=false`).
- Migration 068 aplicada em **hml**; 069 (pg_cron da sonda) em nenhum ambiente.
  Produção não foi tocada.
- O plano §7 continua aberto e é o resto do risco de banimento — **aquecimento
  de chip novo** (rampa no `teto_diario`, que já existe na tabela), variação de
  texto e janela humana. Proxy é o IP; o comportamento é o que mais denuncia robô.

## [Não versionado] - 2026-08-26 (Fix: "Comissão por canal" mostrava Outros 100%)

O donut do dashboard colapsava tudo em "Outros" para quem não tinha CSV de
cliques do período. O correto no dia conferido era Instagram / Others / Websites.

### Fixed

- **O sync gravava o balde amplo em vez do canal de origem.** `channelType`
  (nível do item) só devolveu três valores em 225 mil linhas: "Social Medias"
  (212.837), null (11.094) e "Shopee Video" (1.350). Como o dashboard descarta
  o genérico "Social medias" de propósito — senão tudo colapsaria nele — sobrava
  "Outros 100%".
  - Passa a usar **`referrer`** (nível do nó): Instagram, Others, Websites,
    WhatsApp — a coluna "Canal" do relatório da Shopee. `channelType` fica como
    fallback, melhor que campo vazio.
  - O campo foi encontrado por introspection do schema GraphQL; não aparecia em
    busca por "channel"/"source". Validado contra o relatório do afiliado no dia
    25/08: Websites bateu exato (14,17) e Instagram/Others na mesma proporção —
    a diferença restante é o filtro de status, que o script de validação não
    aplicava.
  - Dois testes de regressão: um garante que `referrer` ganha de `channelType`
    quando ambos vêm, outro garante o fallback quando `referrer` vem vazio.

### Descoberto no caminho

- **`mcnManagementFeeRate` e `linkedMcnName` existem na API** (retornaram
  "8.00%" e "Uno Hub"). É a taxa de contrato do afiliado, que até então não
  estava em lugar nenhum — destrava medir o fee de cada conta sem estimativa.
- **O upsert por `row_hash` atualiza `commission` e `channel`** em conflito
  (`dataset_row_repository.py`). Ou seja, um full refresh de 90 dias corrige o
  histórico dos dois bugs sem UPDATE manual. Ressalva: o `row_hash` do sync
  inclui um contador `seq` por execução — se a API devolver quantidade diferente
  de nós para o mesmo item, o re-sync insere em vez de atualizar.

## [Não versionado] - 2026-08-26 (Fix: upload de CSV sumia quando o broker caía)

Alunas relataram que o upload de cliques "não vai". Testaram outro navegador,
limparam cache, trocaram de arquivo — nada. Do lado de cá: 33 uploads de 4
alunas presos em `pending`, `row_count` 0, sem mensagem de erro nenhuma.

### Fixed

- **Upload de CSV virava órfão quando o broker estava inacessível.** A rota cria
  o dataset como `pending`, enfileira no Celery e devolve 201. O `try/except`
  cobria só Redis com autenticação inválida — com o **hostname do Redis sem
  resolver** (`Error -3 ... Temporary failure in name resolution`), a task nunca
  era aceita e o arquivo ficava no limbo: a tela girava para sempre e nada
  indicava o que houve.
  - `POST /clicks/upload` e `POST /datasets/upload` agora capturam falha de
    broker (`kombu.OperationalError`, `redis.ConnectionError`) e processam o CSV
    **inline num BackgroundTask**, no próprio processo da API — mesmo padrão que
    o cron da Shopee já usava (`internal.py`, "Celery indisponível — fallback
    inline"). O upload passa a funcionar com o broker fora do ar.
  - O processamento inline sempre termina em estado terminal: erro grava
    `status="error"` + `error_message`, nunca deixa em `pending`.
  - **Por que só apareceu agora:** arquivo até `CSV_SYNC_MAX_BYTES` (2 MB) já
    era processado na própria request e funcionava. Só quem passava do limite
    caía na fila e sumia — daí "ontem foi e hoje não", conforme o tamanho do CSV.
  - Teste de regressão em `test_click_upload_estado_terminal.py`, ao lado do bug
    irmão de 04/08 (upload preso em "pending" por arquivo sem linha válida).

### Sabido, não corrigido aqui

- **A causa da queda é de infraestrutura**: a API não resolve o hostname do
  Redis. Enquanto isso não for arrumado, tudo que depende do Celery segue sem
  worker — o sync da Shopee cai no fallback inline e outras tasks (roteiros,
  fan-out) não rodam. Este fix impede o upload de sumir em silêncio; não
  substitui consertar o broker.
- **Os 33 datasets já órfãos** continuam em `pending` e precisam ser
  reprocessados ou marcados como erro.

## [Não versionado] - 2026-08-26 (Fix: sync Shopee gravava a comissão errada)

Reportado por um aluno com rede/hub (Uno Hub): a Shopee mostrava R$ 830,00 de
comissão no dia e o MarketDash mostrava R$ 902,17.

### Fixed

- **O sync da Shopee gravava a "Comissão total do pedido" no lugar da
  "Comissão líquida do afiliado".** O serviço lia
  `estimatedTotalCommission`, que é a comissão **antes** do Fee de gestão da RM.
  Quem tem rede/hub vinculado via a comissão inflada pelo fee (8% no caso
  medido) em todas as telas — Dashboard, Campanhas, lucro e ROAS.
  - Passou a usar `netCommission`, que já era pedido na query GraphQL e nunca
    era lido. Validado em produção, pedido a pedido, contra o relatório do
    afiliado: `netCommission` == "Comissão líquida do afiliado(R$)" e
    `estimatedTotalCommission` == "Comissão total do pedido(R$)", com as cinco
    casas decimais batendo.
  - **Por que passou despercebido:** para afiliado SEM rede/hub os dois campos
    são idênticos. Só quem tem RM com fee > 0 via a diferença. O comentário no
    código afirmava que `estimatedTotalCommission` equivalia à líquida — era
    falso, e agora está corrigido no lugar.
  - **O upload de CSV sempre usou a coluna certa**
    (`csv_service.py`: `"commission": {"comissao_liquida_do_afiliado_r"}`), então
    as duas fontes do mesmo produto discordavam entre si.
  - Teste de regressão: `test_grava_comissao_liquida_do_afiliado_e_nao_a_total`
    mocka os dois campos com valores **diferentes** — os mocks antigos usavam o
    mesmo valor nos dois e passavam com o campo errado.

### Pendente (não incluído neste fix)

- **Histórico já gravado continua inflado** para quem tem RM: a correção só vale
  para as próximas sincronizações. O backfill depende de saber a taxa de
  contrato de cada afiliado, que hoje não é persistida.
- **Pedidos "Não pago"**: o sync grava a comissão estimada deles, enquanto o
  relatório da Shopee traz zero. Hoje isso não aparece no KPI só porque
  `UNPAID` está fora da allowlist de status.

## [Não versionado] - 2026-08-26 (Fix: token do Facebook expirado deslogava a usuária)

Reportado por uma aluna: as campanhas pararam de aparecer e, ao abrir
Configurações → Facebook, o painel voltava sozinho para a tela de login. Ela
ficava presa — a única tela onde daria para reconectar a conta era justamente
a que a expulsava.

### Fixed

- **401 de integração externa derrubava a sessão do MarketDash.** Quando o
  token do usuário no Facebook morria (expirou, senha trocada, app removido),
  a Graph API devolvia `code 190` e o backend traduzia isso para **401**. Para
  o `fetchWithAuth`, 401 significa "a sessão morreu": ele renovava o token do
  Supabase, repetia a request, tomava 401 de novo (o problema era o token do
  *Facebook*) e então limpava o storage e redirecionava para `/login`. Como
  `GET /facebook/ad-accounts` roda **ao abrir a aba**, o logout era imediato e
  a usuária não conseguia chegar no botão de reconectar.
  - Backend: `code 190` agora devolve **409** com
    `detail: {code: "facebook_token_invalido", message}` — 401 volta a
    significar só "sessão do MarketDash inválida". `_access_token` usa o mesmo
    código quando a integração já está inativa, então a tela trata um caso só.
  - Frontend: se a sessão foi **renovada com sucesso** e a request ainda
    devolve 401, o `fetchWithAuth` não desloga mais — devolve a resposta para
    o chamador. Rede de segurança para qualquer 401 de terceiro no futuro.
  - Tela: com o token morto, o card do Facebook mostra **"Conexão expirada"** e
    **"Reconectar com Facebook"**, em vez do badge verde "Conectado" com a
    lista de contas vazia. A integração também é marcada como inativa no
    banco, para o status parar de mentir.

## [Não versionado] - 2026-08-26 (Grupos WhatsApp F6 — link de entrada e eventos)

Fecha a outra ponta da corrente: **anúncio → clique → entrada no grupo**.
Com a F5 (oferta → comissão por grupo), o ciclo do módulo está inteiro.

### Added

- **Link de entrada** `/g/{slug}` (migration 063): roteia a pessoa para um
  grupo com vaga e registra o clique numa transação só — `FOR UPDATE SKIP
  LOCKED` garante que dois cliques simultâneos não recebam o mesmo "último
  lugar". Estratégia **sequencial** (enche por posição) ou **aleatória**
  (distribui — permite comparar conversão entre grupos com o mesmo tráfego);
  grupo lotado sai da rotação sozinho; `abertura_automatica` abre o próximo
  da fila na hora; sem vaga, página "vagas esgotadas".
  **Servido pelo backend**, não pelo SPA: o crawler do WhatsApp não executa
  JS (a prévia só existe com OG tags server-side) e o pixel precisa disparar
  antes do redirect. O frontend encaminha `/g/*` por proxy (nginx + vite),
  preservando o domínio da spec.
- **`/g/preview/{slug}`**: roteia igual, grava `is_teste` e **não entra em
  métrica** — a afiliada testa o próprio link sem sujar o número que mostra
  ao gestor de tráfego. Dedup de 60s e detecção de bot vêm do mesmo lugar do
  `/l/{slug}`.
- **Entradas e saídas de grupo** via webhook `group.v2.participants`: é a
  única forma de saber QUEM entrou e QUEM saiu — diff de snapshot só dá
  contagem líquida e não sustenta "entraram e ficaram". Entrada até 15min
  depois de um clique roteado ao mesmo grupo é atribuída ao link; o resto é
  orgânica. **LGPD**: o número vira `sha256(jid+salt)` no handler, antes de
  qualquer persistência — número de terceiro nunca toca o banco.
- **Snapshot diário** (migration 064, cron 03:00 BRT) reaproveitando o sync
  da F1, + **reconciliação de sessões órfãs** no WAHA (sessão sem dono
  consome RAM para sempre).
- **Abas Link de entrada e Atividade** na campanha, com os avisos que a spec
  exige: prévia cacheada pelo WhatsApp vale só para novos compartilhamentos,
  teste fora da métrica, e "só registramos a partir de agora".
- **Política de privacidade** com a seção do módulo (o que guardamos do
  número da afiliada, o que NÃO lemos, e o código irreversível de terceiros).

### Revisão (nível alto) — corrigido antes do commit

- **O link público apontava para o host do frontend**, onde a rota não existe:
  todo link cairia no NotFound. Resolvido com proxy `/g` no nginx e no vite.
- Trocar de aba destruía o formulário do link (inclusive banner já enviado)
  mesmo com "alterações não salvas" na tela.
- Falha ao atualizar a Atividade apagava o feed já carregado.
- Dois espaços perdidos no JSX da política e a data de atualização.

## [Não versionado] - 2026-08-26 (Grupos WhatsApp F5 — Ofertas e Marketplaces)

Fecha a corrente que é o diferencial do módulo: **buscar a oferta na Shopee →
short link com o SubID do grupo → comissão volta carimbada com o grupo que a
gerou**. Validado ponta a ponta contra a API real.

### Added

- **Busca de ofertas** (`productOfferV2` — código novo, não existia no repo):
  tela `/dashboard/ofertas` com busca por termo, filtros em chips (ordenação,
  comissão mínima, preço máximo, desconto), cards com preço, desconto,
  comissão em % e R$, vendas, e "Enviar agora" que abre o Envio rápido já
  preenchido com o texto e a URL da oferta.
  ⚠️ **A tela é busca por termo, não catálogo navegável** — limitação real da
  API: sem `keyword` o retorno vem vazio mesmo com categoria, e `sortType` é
  ignorado em busca por categoria. Quando a afiliada escolhe só categoria,
  usamos o nome dela como keyword. Os filtros de comissão/preço/desconto são
  aplicados sobre a página exibida (a API não filtra por eles) — e a tela diz
  isso, em vez de fingir que filtrou o catálogo.
- **Integrações de marketplace** (migration 062): tabela `integracoes` com N
  contas de N marketplaces por afiliada, credencial cifrada, **sem conceito de
  "principal"** — a integração certa vem do marketplace detectado na URL; com
  2+ contas ativas do mesmo provedor a afiliada escolhe pelo nome. Tela em
  Configurações › Integrações › Marketplaces (adicionar/ativar/remover),
  preservando o card de sincronização existente (status, "Sincronizar agora",
  "Desconectar").
  **Migração em dois deploys**: este grava nas DUAS tabelas e ainda lê da
  antiga; o próximo vira a leitura. `shopee_integrations` não é dropada.
  Backfill aplicado em hml: 25 credenciais.
- Só marketplaces com API de afiliado assinada entram no catálogo — Mercado
  Livre e SHEIN não têm (o concorrente converte por extensão de Chrome);
  prometer sem API vira dívida de suporte.

### Revisão (nível alto) — corrigido antes do commit

- **Segunda conta sem nome sobrescrevia a primeira** em silêncio (o upsert é
  por usuária+marketplace+nome): agora o campo sugere "conta 2" e confirma
  antes de substituir credencial de um nome existente.
- Busca sem sequenciamento: "Carregar mais" + troca de filtro deixava a
  resposta antiga anexar ofertas que o filtro novo excluiu (e bagunçava a
  paginação) — agora só a resposta do pedido atual é aplicada.
- Duplo clique em "Remover" disparava dois DELETEs; o segundo dava 404 e a
  tela dizia que falhou uma remoção que funcionou.

## [Não versionado] - 2026-08-26 (Grupos WhatsApp F4 — roteiros, templates e IA)

Fecha o ciclo de criação: agora dá para montar a régua de um lançamento
inteiro, com variação de texto por grupo. Sem migration nova (as tabelas
vieram na 060).

### Added

- **Editor de roteiros** (aba Roteiros da campanha): lista ordenada de passos
  **sem canvas** — cada passo abre um painel com Quando (âncora/relativo),
  O quê (texto/mídia/oferta/ação) e Para quem; reordenar, remover, duplicar.
  **Preview obrigatório** antes de agendar, com horários resolvidos, total de
  mensagens, duração estimada e avisos em destaque; avisos exigem confirmação
  explícita ("Agendar mesmo assim?").
- **Templates com variações ponderadas** (menu Templates, MAX-only, hml-only):
  CRUD, peso por variação, soft-delete (passo de roteiro pode referenciar) e
  ajuda curta explicando que `{link}` é o que atribui a comissão ao grupo.
- **IA que gera variações** — SÓ na tela de templates, nunca no envio: no
  disparo o motor sorteia uma variação pronta (custo zero, latência zero, sem
  risco de inventar preço). Variação que perde um placeholder é **descartada**,
  não corrigida. Sem chave configurada, o botão simplesmente não aparece.
- **Ações de grupo no motor**: renomear grupo via WAHA (a régua "ABRE ÀS 20H"
  → "ABERTO") e abrir/fechar entrada como flip local em `campanha_grupos`,
  sem tocar no WhatsApp.

### Fixed

- **`PUT /roteiros/{id}/passos` era impossível de chamar**: `payload: list`
  sem o tipo do item fazia o FastAPI tratar o corpo como multipart/form-data —
  todo JSON virava 422 com `input: null`. Achado pela tela ao tentar salvar;
  agora há teste de contrato sobre o OpenAPI das 3 rotas que recebem lista.
- **O prompt da IA quebrava 100% das gerações**: `str.format()` colidia com as
  próprias chaves (`{link}`, `{produto}`) que o prompt manda preservar.
- `AutomacaoEditor.tsx`: `md:pl-64` → `md:pl-72` (bug conhecido, a barra fixa
  ficava 32px por baixo da sidebar).

### Revisão (nível alto) — corrigido antes do commit, com regressão

- **403 de rename desconectava o número inteiro** (virava `auth`, fatal) e
  **erro 5xx desativava o grupo**: um passo sobre 50 grupos podia desativar os
  50. Agora "sem permissão"/"erro de ação" pulam a linha, sem punir número nem
  grupo.
- `sem_permissao` contava para o disjuntor: 5 grupos sem admin derrubavam a
  sessão.
- "Sou admin" passou a vir do **vínculo por número** (N:N), não do flag
  agregado do grupo — com 2 números o último sync sobrescrevia e dava falso
  positivo/negativo.
- Flip local (abrir/fechar entrada) não paga mais pausa anti-ban.
- `estilo` da IA aceitava texto livre interpolado no prompt (injection paga
  com a chave da empresa) e ação inválida só falhava no disparo — ambos
  validados no schema.

## [Não versionado] - 2026-08-26 (Grupos WhatsApp F3 — motor de envio)

O coração do módulo: roteiros, execuções e o motor que manda de verdade.

### Added

- **Roteiros** (migration 060): sequência de passos com **âncora + offsets
  relativos** e conteúdo polimórfico (texto/mídia/oferta/ação) desde o
  primeiro dia — é o que faz o mesmo motor atender a fila de ofertas da
  afiliada de achadinho E a régua de lançamento. Preview obrigatório com
  horários resolvidos, total de mensagens, duração estimada e **avisos**
  ("relativo cai depois da próxima âncora"); duplicar roteiro recalcula tudo
  a partir da nova data-âncora.
- **Motor de envio**: task Celery `priority=9` auto-fatiada (~15min, re-agenda
  a si mesma), **claim atômico** (`UPDATE ... FOR UPDATE SKIP LOCKED
  RETURNING`) — dois workers nunca pegam a mesma linha; commit por linha
  (progresso ao vivo); linha presa em `enviando` vira `falhou` e **nunca é
  reenviada**; rodadas de 2 mensagens com pausa 8-20s e jitter (ritmo é env,
  nunca UI — a afiliada vê só a estimativa de duração); tetos em cascata
  (plano 240 → global 5000/dia → 80 por número); disjuntor por número com
  **failover** para outro número da afiliada; `grupo_invalido` pula a linha e
  desativa o grupo, sem abortar o lote. Auditoria em `sync_runs`
  (`source='roteiro_execucao'`) → aparece no painel `/admin/sincronizacoes`.
- **Tick pg_cron a cada 5min** (migration 061, só no banco que envia):
  flip atômico `agendada→enviando` sobre índice parcial — dois ticks não
  duplicam — e **resgate de execuções estagnadas** em `enviando`.
- **Envio rápido**: roteiro de 1 passo, mesma tabela e mesmo motor, disparo
  imediato (priority 0, sem esperar o tick) ou agendado.
- **Janela de horário por usuária** (Configurações › WhatsApp › Envio):
  padrão 08:00–22:00, configurável por dia da semana com pausa no meio do
  dia e toggle geral; as 3 regras de borda da spec valem no worker.
- **Frontend**: modal de Envio rápido (grupos com busca, upload de imagem,
  agora/agendar, confirmação em linguagem natural) que vira tela de
  progresso com polling de 4s, pausar/retomar/cancelar.

### Revisão (2 rodadas) — corrigido antes do commit, com regressão

- **Blocker**: o tick chamava `_validate_cron_secret(request)` com a
  assinatura errada — TODO tick daria 500 e nada seria enviado.
- Execução estagnada em `enviando` (worker morto ou broker fora) não tinha
  volta: o tick agora resgata.
- Execução 100% `pulada` congelava em `agendada` com `proxima` NULL —
  agora conclui na hora.
- Janela com todos os dias inativos gerava **livelock** de 5 em 5 minutos —
  `proxima_abertura` devolve None, o motor pausa com motivo, e salvar essa
  config passou a ser 422.
- Teto diário virava `pausada` (exigia clique e matava roteiro de vários
  dias) — agora **parqueia para a abertura do dia seguinte**.
- `agendar_para` sem fuso era lido como UTC (disparo 3h adiantado) — naive
  agora é BRT.
- `campanha_id` de outra usuária era aceito (prefixo/sufixo alheio vazaria
  para dentro da mensagem enviada) — ownership validado.
- Coluna `whatsapp_envio_config` entrou nas safe migrations do boot: sem
  ela, código antes da migration quebraria TODA query de UserSettings.
- Frontend: falha ao carregar grupos dizia "você não tem grupos" e mandava
  reconectar número já conectado; fechar o modal com envio em curso perdia
  o acompanhamento para sempre; envio adiado pela janela ficava girando em
  0/0 sem saída.

## [Não versionado] - 2026-08-26 (Infra hml: WAHA no Coolify, Evolution excluída)

- **WAHA no ar em homologação**: app docker-image `waha-hml` (engine GOWS) no
  Coolify, volume de sessões, "Consistent Container Names" ligado; a API fala
  com ele pela rede interna (`http://hw88gc8ocsko04k8wkocs8kc:3000`) — sem
  proxy, sem porta pública. Envs `WAHA_*` na API hml; `EVOLUTION_*` (vazias)
  removidas; **os 2 serviços Evolution do Coolify foram excluídos**.
- E2E validado: `GET /api/v1/whatsapp/instancia` em hml criou a sessão
  `mkdytjpresumo` no WAHA e devolveu o QR real — falta só escanear.
- Gotcha de infra descoberto: há um registro de servidor QUEBRADO no Coolify
  ("busy", IP `http:31.97.22.173`) — deploy nele falha com "Server is not
  functional"; recurso novo vai sempre no `localhost`. Detalhes no runbook
  `docs/whatsapp-waha.md`.

## [Não versionado] - 2026-08-25 (Grupos WhatsApp F2 — Campanhas + rename Anúncios)

Terceira fase do Módulo de Grupos: a entidade **Campanha** (conjunto de grupos
com estratégia de entrada e configuração própria) e a renomeação que a spec
exige no MESMO deploy — o menu "Campanhas" (tráfego pago) virou **"Anúncios"**.

### Added

- **Campanhas de grupos** (MAX-only, hml-only): CRUD + composição de grupos
  com posição (ordem do rotativo do link de entrada, F6), toggle `aberto` por
  grupo, estratégia sequencial/aleatória, abertura/reabertura automática,
  prefixo/sufixo e modo de imagem POR campanha (spec §10.4: nunca perguntar a
  cada envio). Arquivar em vez de deletar (histórico de atribuição); grupo
  pode estar em N campanhas. Rotas `/api/v1/campanhas-grupos*` com ownership
  como dependency; migration **059** (RLS + policy) aplicada em hml ANTES dos
  models — protocolo da F1 honrado desta vez.
- **Frontend**: menu ganha "Campanhas" [Novo] ao lado de "Anúncios" (a divisão
  gasto × grupos que a spec §3.1 queria comunicar sem explicação); lista com
  banner de pré-requisito (conectar número) e criação em modal; detalhe com
  abas Visão geral (form + radio-cards de estratégia) e Grupos (reordenar
  ▲▼, abrir/fechar, adicionar em modal com busca, salvar ordem via PUT).
- **Config de menu unificada** (`shared/config/dashboard-menu.ts`): sidebar e
  bottom nav consomem a MESMA lista (quita o débito anotado na F0); gate de
  ambiente (`hmlOnly`) vale para as duas metades do nav; `PATH_TO_MENU`
  (órfão) removido de plans.ts.
- Limite de plano `campanhas_grupos` (Max: -1; demais: 0) em plans.py+plans.ts.

### Changed

- **"Campanhas" → "Anúncios"** no menu (desktop + mobile) e no título da tela
  de tráfego pago. Path e menuKey preservados (`/dashboard/campanhas`,
  `campanhas`) — deep-links e gating continuam valendo. Textos internos da
  tela ficam para a F7 (refatoração §8.2).

### Revisão (2 rodadas) — corrigido antes do commit, com regressão

- Payload do PUT com `grupo_id` repetido estourava a PK composta (500 no meio
  do "Salvar ordem") — dedup pela última ocorrência.
- Nome só de espaços passava pelo `min_length` do Pydantic e criava campanha
  sem título — 422 no service.
- **Desarquivar re-conta o limite do plano**: "arquivar libera vaga" (testado)
  implicava que o caminho de volta furava a conta.
- Tabs principais do bottom nav não passavam pelo gate de ambiente da config
  unificada; PATCH pulava a camada de service para contar grupos.

⚠️ **Promoção develop→main exigirá aplicar 058 E 059 em produção
imediatamente ANTES do merge** (o create_all criaria as tabelas sem RLS e o
IF NOT EXISTS posterior viraria no-op) — conferir no banco, não na memória.

## [Não versionado] - 2026-08-25 (Grupos WhatsApp F1 — WAHA, números e grupos)

Segunda fase do Módulo de Grupos: infraestrutura de WhatsApp trocada de
Evolution API para **WAHA + engine GOWS** (decisão 25/08 — ~60MB/sessão vs
300-500MB do Baileys; WAHA gratuito/Apache-2.0; Evolution 2.4 passou a exigir
ativação com telemetria). Migração TOTAL: o resumo diário também migrou e o
Evolution saiu do código.

### Added

- **`waha_client.py`** — fronteira única com o WAHA, mesmo desenho do antigo
  EvolutionClient (`_pedir()` + MockTransport, `ErroWhatsapp` tipado): sessões
  (criar/QR/status/logout/delete, idempotente), grupos paginados, envio de
  texto/imagem a `@c.us` e `@g.us` (JID validado, NUNCA passa por
  `normalizar_numero`), classificação de erro com `grupo_invalido` (pula a
  linha, não derruba o lote). Validado contra WAHA real local (GOWS).
- **Números da afiliada** (multi-número, MAX-only): `whatsapp_instancias` com
  nome de sessão `mkd{ref4}u{user_id}x{hex4}` (prefixo do ambiente impede
  fratricídio hml×prod no mesmo servidor), limite do plano (3) + cap global
  (`WHATSAPP_MAX_INSTANCIAS_GLOBAL=60`, proteção de RAM), QR via API, remoção
  com soft-delete. Rotas `/whatsapp/instancias*` (`require_plan("max")`).
- **Sync de grupos** (`/whatsapp/instancias/{id}/sincronizar-grupos`):
  upsert por `(user_id, jid)`; **`sub_id` (`wg{base36(id)}`) e `custom_link`
  (tag `whatsapp`) nascem NO SYNC** — atribuição nunca se perde; grupo que
  some vira `ativo=false`, NUNCA é deletado; vínculo N:N grupo↔instância;
  convite puxado automaticamente quando admin; `permite_envio` lê a config
  real do grupo (admin OU grupo aberto). Auditoria em `sync_runs`
  (`source="whatsapp_grupos"`) — aparece no painel `/admin/sincronizacoes`.
- **Webhook multi-sessão** (`POST /api/v1/whatsapp/webhook`): dispatch por
  `event` + nome da sessão; `session.status` espelha conexão/queda da
  instância; sessão de outro ambiente é ignorada; SAIR só na sessão do resumo
  e nunca a partir de mensagens `@g.us`. Sessões de aluna assinam SÓ
  `session.status` — conteúdo de mensagem não chega ao backend (LGPD).
- Migration **058** (idempotente, RLS + policies por `user_id`, índice único
  parcial de `sub_id`) — **APLICADA em hml**; produção só no deploy da fase.
- Limites de plano `whatsapp_numeros` (Max: 3) e `whatsapp_grupos` (Max: -1)
  em `plans.py` + `plans.ts` + plan context.
- Runbook `docs/whatsapp-waha.md` (substitui `whatsapp-evolution.md`), serviço
  `waha` no docker-compose (perfil `whatsapp`; tag `arm` local, `latest` no
  Coolify), 19 testes novos (client contra MockTransport, sync com SQLite,
  roteamento de webhook, service de instância).

### Changed

- **Resumo diário migrado para o WAHA**: `whatsapp_envio_service`/optin/runner
  agora falam `chatId` (`numero@c.us`) via `waha_client`; tela do admin
  mantém o contrato (`estado: "open"`) para não mexer no frontend.
  ⚠️ Operacional: o número do resumo precisa ser **pareado de novo** (QR) em
  cada ambiente — sessão da Evolution não migra (importar sessão é vetor de
  sequestro; decisão consciente).
- Links de grupo (tag `whatsapp`) ficam **fora do limite** de links do plano
  e **fora da listagem** de Meus Links.
- Config: `EVOLUTION_*` removidas; entram `WAHA_URL/API_KEY/SESSAO_RESUMO/
  WEBHOOK_TOKEN/WEBHOOK_URL` (webhook SEMPRE da URL pública configurada).

### Removed

- `evolution_client.py` e o serviço `evolution` do compose.

### Revisão de código (8 ângulos) — endurecimentos aplicados antes do commit

Os que valiam bug real, todos com teste de regressão (+12 testes):
- **403 no envio a grupo** era classificado como `auth` (fatal, mata o lote) —
  agora é `grupo_invalido` (pula a linha); 403 fora do envio continua `auth`.
- **422 do WAHA no criar sessão** era engolido como "já existia" → QR eterno
  sem log; agora só 409/"already exists" é conflito, o resto sobe tipado.
- **Linha local criada antes da sessão WAHA**: outage do WAHA queimava o
  limite do plano com linhas órfãs — ordem invertida (WAHA primeiro).
- **Evento atrasado ressuscitava número removido** (retry do webhook após o
  logout) — `por_nome` exclui removidas e o handler ignora `removida`.
- **Filtro de Meus Links por tag "whatsapp"** colidia com tag digitável pela
  usuária (link sumia da tela + burlava o limite) — trocado por filtro pela
  FK `whatsapp_grupos.custom_link_id`.
- **`permite_envio` assumia "aberto"** quando o payload omite os flags de
  anúncio — agora conservador (só admin garante).
- **Sessão STOPPED/FAILED** ficava em "aguardando" para sempre — o QR religa
  via `iniciar_sessao()`.
- **PUT de webhook a cada abertura da tela do admin** reiniciava a sessão do
  resumo (derrubaria lote em andamento) — agora compara a config antes.
- Modelo ganhou a `UNIQUE (user_id, jid)` da migration (guard sobrevive à
  corrida do create_all); índices novos na 058 (`instancia_id` na junção,
  `(user_id, ativo)` e `custom_link_id` em grupos) — reaplicada em hml.
- Eficiência: cliente HTTP persistente, 1 leitura de sessão por poll do QR
  (eram 3), preloads no sync (mata N+1), desativação de órfãos em UPDATE
  único, poll conectado não gera mais transação de escrita.
- Camadas: ownership de instância virou dependency do FastAPI, leituras
  passam por service, convenção de nome de sessão com fonte única
  (`prefixo_de_sessao`/`pertence_a_este_ambiente` + teste gerador↔roteador).

### Frontend (Configurações › WhatsApp › Números)

- Nova seção **Números** (grupo WHATSAPP, hml-only): conectar número por QR
  (modal com polling de 5s, QR real do WAHA, aviso anti-ban), limite exibido
  no ponto de uso ("Conectar número (usados/3)"), badge de status por
  instância, sincronizar grupos com toast do resultado, remoção com
  confirmação destrutiva; lista de grupos (tabela desktop + DataCard mobile,
  busca client-side, coluna "Envio" pelo `permite_envio` real do grupo).
  Plano sem o recurso vê banner de upgrade. `plans.ts` espelha os limites
  novos no MESMO commit (regra do repo). E2E validado contra WAHA local:
  criar → QR real → remover (soft-delete + sessão deletada no WAHA).

### Incidente evitado (registro do protocolo)

O app local roda com `--reload` apontando para o banco de **hml**: ao salvar
os models novos, o `create_all` do boot criou as 3 tabelas em hml ANTES da
migration. O Supabase habilitou RLS por padrão (deny-all sem policy — failsafe
que segurou a exposição), mas sem policies nem o índice parcial. Remediação:
058 aplicada por cima (idempotente) e verificada (`pg_policies` + índices).
**Lição para o protocolo**: em dev local contra hml, aplicar a migration
ANTES de escrever o model — o "deploy" local é instantâneo.

## [Não versionado] - 2026-08-25 (Grupos WhatsApp F0 — fundações de UI)

Primeira fase do Módulo de Grupos de WhatsApp (plano consolidado aprovado em
25/08: spec Luiz v1.1 + plano técnico 23/08). Sem migration; deployável em
produção.

### Changed

- **Menu lateral compacto**: fonte `text-sm`, item `py-2`, ícone `w-4 h-4` —
  prepara os 10 itens do módulo de grupos sem scroll em 1366×768 e 1280×720
  (medido via Playwright: `scrollHeight == clientHeight` nos dois tamanhos).
- **"Automação Instagram" → "Instagram"** no menu (spec §3.1: itens nomeiam a
  coisa, não a função).
- **Item ativo por prefix-match** (exceto `/dashboard`): rota aninhada como
  `/dashboard/automacoes/nova` mantém o pai destacado — pré-requisito das abas
  da Campanha (F2). Aplicado na sidebar e no bottom nav.
- **Configurações: abas horizontais → sub-navegação vertical agrupada**
  (CONTA / INTEGRAÇÕES / WHATSAPP / CÁLCULOS), spec §3.3. Shopee virou seção
  "Marketplaces"; Facebook+Instagram viraram "Canais"; Impostos e Assinatura
  intocados na lógica. No mobile: lista agrupada → subtela com voltar. A seção
  agora **deriva da URL** (`?tab=`), então deep-links antigos
  (`?tab=shopee|facebook|instagram|whatsapp`) seguem funcionando, o Back do
  celular volta pra lista e a seção é compartilhável.
- **MobileBottomNav com gating de plano** (não tinha nenhum): cadeado + modal
  de upgrade, como a sidebar; Instagram entrou no drawer "Mais" (só fora de
  produção, mesmo gate da sidebar).

### Fixed

- **`useIsMobile()` com valor inicial síncrono**: antes o primeiro render era
  sempre "desktop" (`useState(undefined)` → `!!undefined`), causando flash de
  layout e montagem/desmontagem dupla de componentes com efeitos de rede no
  celular (pior caso: retorno do OAuth do Facebook com `?code` montava o
  handler duas vezes).
- **Cadeado de plano só com contexto carregado** (sidebar + bottom nav): o
  fallback do store é "essencial", e durante o fetch (ou após falha dele) o
  assinante Max via cadeado em Links/Instagram e caía no modal de upgrade em
  vez de navegar. Agora o clique fica liberado até o contexto real chegar —
  seguro porque toda rota tem `RequirePlan`.

### Removed

- **`FeedbackFloatingButton` + `useNavigationTracker`**: dead code — nunca
  foram renderizados em lugar nenhum (refatoração #6 da spec).

## [Não versionado] - 2026-08-24 (2º vazamento do merge: card de veiculação)

Luiz reportou o card **"Onde seu anúncio está rodando"** aparecendo em
`/dashboard/campanhas` de **produção** — ele só tinha visto isso em
homologação. Confirmado: o card não existia em `main` antes do merge de 22/08
(`git ls-tree a6ecc3b` não tem `PlatformBreakdownCard.tsx`), foi de carona no
mesmo merge de branch inteiro que levou a Automação Instagram, e **não tinha
gate nenhum** — nem `isProductionHost()`, nem plano, nem flag. Toda aluna com
pelo menos 1 campanha via.

O gate emergencial de 23/08 não pegou este caso porque cobriu só a Automação
Instagram (menu `automacoes`, aba de Configurações, rotas `/dashboard/automacoes*`).
Este card é **Marketing API do Facebook** — integração e token diferentes —
dentro da tela Campanhas, que ninguém revisou.

### Fixed

- **Card de veiculação gateado por `isProductionHost()`** em `Campanhas.tsx`.
  Não é só posicionamento — ele **briga com os KPIs da própria tela**:
  - **ROAS**: o card faz `revenue / spend` (GMV da Shopee ÷ gasto **sem**
    imposto) e mostrou **24,75x** dois centímetros abaixo do KPI "ROAS Real"
    marcando **1.08x**. O comentário do próprio KPI
    (`campaign_service.py:193-194`) diz que essa fórmula *"inflava o ROAS
    (27x)"* e foi abandonada de propósito. O card a reintroduziu.
  - **Gasto e Lucro**: `PlatformBreakdownService` nunca chama `_tax_rates()`,
    então exibe gasto cru (R$ 7.831,04) enquanto o KPI exibe com imposto
    (R$ 8.914,08) — a diferença é exatamente o imposto de anúncio. O docstring
    do service promete *"o total somado bate exato com a tela Campanhas"*:
    falso para quem tem imposto configurado.
  - **Empty-state não some**: sem dados de placement o card renderiza um Card
    prometendo *"aparecem depois da próxima sincronização com o Meta"* — uma
    promessa de funcionalidade não anunciada.
  Fica em homologação até as fórmulas baterem com as da tela.
- **`/exclusao-de-dados`** — rota **pública**, sem login, afirmava *"Recebemos
  e já processamos o pedido de exclusão dos dados da sua conta do Instagram"*
  para **qualquer visitante** que abrisse a URL, sobre uma feature desligada em
  produção. O texto de confirmação passou a depender do `?code` do callback da
  Meta; sem código a página explica o que é e como pedir, sem afirmar nada.

### Migration 057 APLICADA EM PRODUÇÃO — e a correção de um registro errado

A entrada de 22/08 afirma que a `057` seria **no-op em produção** porque as
migrations 043/044 nunca foram aplicadas lá. **Isso estava errado.** Query real
no banco de produção (`db.iprdyorxqdiivthtcvxf`, 24/08) mostrou as três tabelas
existindo:

```
ai_diagnostics | ai_diagnostic_messages | ai_credit_ledger   → todas presentes, 0 linhas
```

A inferência "a migration não rodou lá, logo a tabela não existe" é inválida
neste projeto por causa do `create_all` (abaixo). O `DROP TABLE IF EXISTS`
evitou que o erro causasse dano, mas a justificativa escrita era falsa.

**Aplicada em produção em 24/08** (dump CSV das 3 tabelas antes, todas vazias —
nenhum dado perdido; a feature nunca foi usada lá porque o menu estava oculto).
Confirmado depois: as 3 tabelas e o índice `ux_ai_diagnostics_gerando_por_usuario`
não existem mais, `users`/`subscriptions`/`campaigns`/`dataset_rows_v2` intactas,
67 usuários no lugar, API com 127 endpoints e 0 de IA.

Estado das outras tabelas de features desligadas em produção (todas presentes,
criadas pelo `create_all`): `instagram_*` e `whatsapp_*` **vazias** — não vale
dropar, o `create_all` as recria no próximo deploy porque os models existem.
`campaign_platform_daily_insights` com **9.815 linhas** — o sync do Facebook vem
gravando placement em produção há dias; é a origem dos dados do card do Luiz.

### RLS em produção — medido, e não é o que a documentação promete

Diagnóstico rodado em 24/08. Todas as tabelas têm `relrowsecurity = true`, mas
**metade tem zero policies** — incluindo tabelas antigas e centrais (`users`,
`custom_links`, `capture_sites`). Não é regressão do merge, é o estado histórico.

O ponto que importa: **a aplicação conecta como `postgres`, que tem
`rolbypassrls = true`** — ela ignora RLS tenha ou não policy. O
`SET LOCAL app.current_user_id` descrito no CLAUDE.md como mecanismo de
isolamento **não protege nada** nessa configuração. O isolamento real é o filtro
por `user_id` em toda query, que continua valendo. A "segunda camada" que a
documentação descreve não existe hoje — anotado como dívida, sem urgência.

### Achado de arquitetura — por que as tabelas existem em produção sem migration

**`Base.metadata.create_all(bind=engine)` roda no boot da aplicação**
(`app/db/base.py:54`). Toda tabela declarada como model SQLAlchemy é criada
automaticamente em produção **a cada deploy** — sem as `POLICY`/`ENABLE ROW
LEVEL SECURITY` que existem só nas migrations.

Isso explica de uma vez três confusões desta semana:
1. Por que `campaign_platform_daily_insights` (migration 051) tem dados em
   produção sem ninguém ter rodado a 051.
2. Por que a nota de memória sobre "049–056 não aplicadas em produção" vinha
   dando errado — o schema chega por outro caminho.
3. Por que a premissa da migration 057 (*"as tabelas de IA existem SOMENTE em
   homologação"*) era frágil: `models/__init__.py` importava os models `ai_*`
   antes do merge, então o `create_all` podia tê-las criado em produção.

**Consequência de segurança**: tabela nascida do `create_all` fica **sem RLS**.
O isolamento primário (filtro por `user_id` em toda query) continua valendo —
RLS é a segunda camada. Diagnóstico pendente: query em produção listando
`relrowsecurity=false` nas tabelas novas.

### Verificado (não é regressão)

- **`sync_gate.py`** (allowlist do Luiz para sync em homologação) é **seguro em
  produção**: `if not is_homologacao(): return True` e `is_homologacao()` só é
  verdadeiro para a ref `ytjpdvjuxtvxacredekk`. Falha aberto, que é o lado certo.
- **Webhooks do Instagram** estão montados em produção e são públicos, mas
  falham **fechados**: sem `INSTAGRAM_APP_SECRET` a verificação de assinatura
  retorna 403 e sem `INSTAGRAM_WEBHOOK_VERIFY_TOKEN` o handshake dá 503.
- **`inline_link_clicks or clicks`** (suspeito dos 97.834 cliques / CPC R$ 0,08)
  **já existia** em `8ebfa59`, antes do merge — débito técnico pré-existente,
  não vazamento.
- **Sync do Facebook não quebrou** com o filtro `effective_status` novo: o
  próprio print do Luiz mostra "Facebook 23/08, 22:02".

## [Não versionado] - 2026-08-22 (Automação Instagram oculta em produção)

Correção de um efeito colateral da entrada abaixo. A remoção da IA foi de
`develop` para `main` num merge de branch inteiro, e `develop` tinha **20
commits de backend e 12 de frontend** acumulados — a **Automação Instagram**
inteira foi junto sem querer. `main` não tinha nenhum arquivo de Instagram
antes disso (confirmado por `git ls-tree` em `8ebfa59`/`a6ecc3b`).

Ela não está pronta pra produção por dois motivos independentes: falta o
**App Review** da Meta (em Standard Access só admin/dev/tester completa o
OAuth — aluna comum trava na autorização) e as **migrations 049-056**, que
criam as tabelas, **não foram aplicadas em produção**. Era código sem schema.

Decisão: **ocultar a UI em produção**, não reverter os 32 commits — assim os
fixes de campanhas/admin que também foram junto continuam valendo. As
migrations 049-056 seguem **não aplicadas** em produção, de propósito.

### Changed

- **`DashboardSidebar.tsx`** — o item "Automação Instagram" some do menu
  inteiro em produção. Voltou o `visibleMenu`, agora filtrando por
  `menuKey !== "automacoes"`. Não vira cadeado: cadeado é gating por plano.
- **`Configuracoes.tsx`** — a aba Instagram some, e o deep-link
  `?tab=instagram` deixa de resolver (cai no fallback "shopee"). Sem isso a
  URL abriria a aba que o botão esconde — mesmo cuidado já tomado com o
  WhatsApp em 11/08.
- **`app-routes.tsx`** — as 4 rotas `/dashboard/automacoes*` não são
  registradas em produção, então caem no 404. Esconder só o item de menu não
  bastava: quem já tivesse a URL entraria e bateria em tabela inexistente.
- Homologação continua com tudo liberado, sem mudança.

### Achado sério — a validação de TypeScript do CI não valida nada

`npx tsc --noEmit`, que é o comando rodado nos dois workflows de deploy do
frontend, **não checa nenhum arquivo de `src/`**. O `tsconfig.json` da raiz
tem `"files": []` e delega tudo para project references
(`tsconfig.app.json`), e sem `-b` o TypeScript não compila as referências.

Provado na prática: com `isProductionHost` usado mas **sem o import**,
`npx tsc --noEmit` retornou **0 erros**, enquanto `npx tsc -b` acusou
`error TS2304: Cannot find name 'isProductionHost'`. Em runtime seria um
`ReferenceError` derrubando o app inteiro — e o CI teria deixado passar.

**Não corrigido nesta rodada de propósito**: `tsc -b` acusa **26 erros
pré-existentes** (`Demo.tsx`, `CategoryBarChart.tsx` e outros), então trocar
o comando do CI hoje quebraria o pipeline. Fica como dívida com o caminho
claro: zerar os 26 e então trocar `tsc --noEmit` por `tsc -b` nos dois
workflows. Enquanto isso, o que de fato valida o frontend é o `vite build`
(compila de verdade) e a validação visual no navegador.

Baseline medido para esta rodada: `tsc -b` dava **27 erros** em `f786e1d`
(antes de qualquer mudança de hoje) e dá **26** agora — nenhum erro novo.

## [Não versionado] - 2026-08-22 (remoção do Diagnóstico IA)

Toda a IA saiu do MarketDash. Era **uma feature só** — o Diagnóstico IA — e a
remoção é de código, não de gating: o menu já sumia em produção desde 11/08 por
`isProductionHost()`, mas aquilo era cosmético. A rota `/dashboard/diagnostico-ia`
ainda abria por URL direta e a API `/api/v1/ai-diagnostics` seguia exposta e
funcional. Agora a rota devolve 404 e os endpoints não existem mais.

Deploy nos dois ambientes: `develop` (hml) e `main` (produção), nos dois repos.

### Removed

- **Backend — 12 módulos de produção** (todos 100% IA, nenhum importado por
  código de domínio): `routes/ai_diagnostics.py`, `services/openai_client.py`
  (único ponto de rede com LLM em todo o projeto), `ai_diagnostic_service.py`,
  `ai_credit_service.py`, `ai_snapshot_service.py`, `ai_prompts.py`,
  `ai_relatorio.py`, `repositories/ai_diagnostic_repository.py`,
  `ai_credit_repository.py`, `models/ai_diagnostic.py`, `ai_credit_ledger.py`,
  `schemas/ai_diagnostic.py`. Mais 7 testes unitários e 2 docs de superpowers.
- **Frontend**: `src/features/diagnostico/` inteiro (5 arquivos) e
  `src/services/ai-diagnostic.service.ts`.
- **Config**: `OPENAI_API_KEY` e `OPENAI_MODEL` saíram de `app/core/config.py`.
  Nenhuma dependência a desinstalar — a chamada era `httpx` cru, sem SDK de LLM
  em `requirements.txt` nem em `package.json`.
- **Planos**: a chave `creditos_ia` sai dos limites dos 3 planos e o menu
  `diagnostico_ia` sai de `pro`/`max` e de `PRO_ONLY_MENUS` — em `plans.py` e
  `plans.ts`, que seguem espelhados.

### Changed

- **Bullet do plano MAX no modal de upgrade** (`UpgradePlanoModal.tsx`):
  "1.000 créditos de IA por mês" → **"Todos os recursos do plano Pro inclusos"**.
  Era o único texto de venda de IA que a aluna via dentro do app logado; deixá-lo
  vira promessa falsa, e apagar sem repor enfraquecia a lista do MAX contra a do Pro.
- **`isProductionHost()` FICA** em `api.config.ts`. Perdeu o consumidor do
  Diagnóstico IA mas continua escondendo a **aba WhatsApp** em produção — e a
  regra de comparação EXATA de hostname (nunca `.includes`) segue valendo.
- **`DashboardSidebar.tsx`**: com o item de menu fora, o `visibleMenu` filtrado
  deixou de existir e o `.map()` voltou a iterar `menuItems` direto.
- Docs e memória dos dois repos atualizadas (`.claude/`, `README.md`,
  `PRODUCT_OVERVIEW.md`, skills de planos e integrações).

### Migration

- **`057_remover_diagnostico_ia.sql`** — `DROP TABLE IF EXISTS` de
  `ai_credit_ledger`, `ai_diagnostic_messages` e `ai_diagnostics` (nessa ordem,
  por causa das FKs), com `CASCADE` levando junto o índice
  `ux_ai_diagnostics_gerando_por_usuario` da 044.
- **Aplicada em homologação** (`ytjpdvjuxtvxacredekk`). Em **produção**
  (`iprdyorxqdiivthtcvxf`) é **no-op**: as migrations 043/044 nunca foram
  aplicadas lá (confirmado por `information_schema` em 11/08, ver entrada
  daquele dia), então não havia nada para derrubar nem dado de IA a perder.
- As migrations 043/044 **ficam no repo** como histórico — a convenção é não
  reescrever migration já aplicada em algum ambiente.

### O que NÃO é IA e continuou intacto

Verificado lendo o código, não por nome de arquivo — vários falsos positivos
de busca textual:

- **Resumo diário do WhatsApp**: mensagem 100% template em f-string, números do
  `KpiService` (SQL determinístico) e alerta por heurística pura. Nada de LLM.
- **Automação de Instagram**: textos digitados pela aluna, com rotação
  round-robin. O "template com botão" é o template `button` da Graph API da
  Meta — envelope de envio, não prompt.
- **"Insight" de cliques** (`custom_link_service`): agregação SQL para gráfico
  de barras. **"insights" de campanha**: nome do endpoint de métricas da Meta
  Marketing API.
- **`campaign_service._health`**: o semáforo saudável/atenção/prejuízo é uma
  função condicional de 5 linhas com limiares fixos. É essa a "análise" que a
  aluna vê nas telas normais — a IA apenas consumia esse rótulo.
- **"Orquestra IA"**: razão social da empresa (6 arquivos legais e de rodapé).

### Segurança

- A `OPENAI_API_KEY` real estava em texto puro em dois arquivos locais não
  versionados (`.env` e `.env.backup-1208`) — as linhas foram removidas.
  **Apagar o arquivo não invalida a chave**: ela continua ativa na conta da
  OpenAI e configurada no ambiente do Coolify. Precisa ser revogada no painel.

### Verificação

- Backend: app importa, **127 endpoints de pé, 0 de IA**; WhatsApp (6),
  Campanhas (8) e Instagram (10) intactos. `pytest`: **564 passam**. As 3 falhas
  de `test_shopee_upsert_additive.py` são **pré-existentes** — reproduzidas
  idênticas no HEAD limpo, causa é a validação numérica de AppID contra
  fixtures antigas, sem relação com IA.
- Frontend: `tsc --noEmit` limpo e `vite build` verde. Os 3 erros de lint são
  pré-existentes, em arquivos não tocados.
- Visual (Playwright, conta `relacionamento@`, contra a API de hml): menu com os
  8 itens restantes e sem Diagnóstico IA; `/dashboard/diagnostico-ia` devolve
  **404**; aba WhatsApp ainda presente em Configurações; **zero menções a IA**
  varrendo Dashboard, Campanhas, Planos e Configurações; mobile conferido.

## [Não versionado] - 2026-08-21 (Automação Instagram — Rodada 2)

### Changed

- **O direct sai como template com botão.** O link deixou de ser colado no meio
  da mensagem e virou um botão (`web_url`) embaixo do texto — link cru parece
  spam, botão parece mensagem de marca. O Card 4 do editor virou três campos:
  **Mensagem** (só texto), **Link** (com o "Inserir link", que agora preenche
  este campo em vez de emendar a URL no fim do texto) e **Texto do botão**
  (limite 20, o mesmo da Meta). O preview do Direct renderiza o botão, sob a
  mesma condição do backend — só aparece com link **e** título, para a tela não
  prometer um botão que o envio não mandaria.
- **Seletor de emoji** nos dois campos de texto: Card 3 (entra na última
  variação) e Card 4 (fim da mensagem). Lista curta escolhida a dedo em vez de
  biblioteca — uma lib de emoji custa centenas de KB numa tela que a aluna abre
  pelo celular.
- **Contador de caracteres alinhado** com o seletor de emoji, na mesma linha de
  base. Antes ficava solto numa linha própria.

### Added

- Migration **056** (`dm_link`, `dm_botao_texto`), só ADD e idempotente. Ambos
  NULL nas automações existentes, que seguem no formato antigo sem migração de
  dado.
- **Interruptor para voltar sem redeploy:** `INSTAGRAM_DM_FORMATO=texto` no
  Coolify + restart faz o envio parar de mandar o botão. A env var é lida antes
  do `feature-flags.json` de propósito — o arquivo é versionado e exigiria
  rebuild de imagem. Valor inválido cai no default, sem desligar em silêncio.
- Validação de publicação: link sem título de botão, ou título sem link, agora
  são **422** com mensagem específica. Antes, link sem título viraria mensagem
  sem botão (com o link voltando ao corpo, calado).

## [Não versionado] - 2026-08-20 (Automação Instagram — Rodada 1 de ajustes)

Validação em homologação com a conta `@promosdabeatrizz_` (relatório do Luiz).
Funcionou ponta a ponta: comentário real → resposta pública → direct em menos de
10 segundos.

### Changed

- **O webhook `comments` entrega antes do App Review.** A doc da Meta exige
  Advanced Access, e planejamos o cronograma em cima disso — na prática o
  comentário real disparou com o app em Standard, provavelmente porque a conta
  está como testadora e o app está Live. O roteiro passa a rodar com comentário
  real e **o screencast do App Review deixa de ser ovo-e-galinha**. Falta
  confirmar se vale para conta fora do painel.
- **Escopo "Próxima publicação" removido** (UI e backend). A amarração era
  preguiçosa — acontecia no primeiro comentário de um post novo —, então com
  dois posts publicados a automação grudava em qualquer um dos dois e a aluna só
  descobria depois; sem comentário, esperava para sempre. "Qualquer publicação"
  cobre o caso sem ambiguidade.
- **Escolha da publicação:** a grade inteira (224 posts na conta de teste)
  empurrava os cards 2, 3 e 4 para fora da tela. Virou uma fileira de 4 posts
  recentes + modal com **busca por legenda** — buscar `ESCOVA` devolve os 8
  posts com a palavra, contra rolar centenas de thumbnails parecidos.
- **Variações da resposta pública:** um campo por variação, com X para remover e
  "+ Adicionar variação" que some na quinta. Antes era uma variação por linha num
  textarea, onde enter duplo criava variação vazia e colar texto multilinha
  bagunçava tudo.
- **Modal "Inserir link" ganhou busca** por nome e slug.
- **Microcopy:** uma frase por card, factual. Saíram as segundas frases
  explicativas/persuasivas e os dois rodapés do card de direct.

### Fixed

- **Caminho errado no checklist de conexão.** O passo 2 mandava para
  "Configurações → Privacidade → Mensagens → Ferramentas conectadas", que **não
  existe** nas versões atuais do app. Corrigido para o caminho verificado no iOS
  (Configurações e atividade → Tipo e ferramentas da conta → Ferramentas de
  mensagem → Controles de mensagem → Pedidos de contato → Ferramentas
  conectadas). É o passo que mais gera chamado — mandar a aluna para um menu
  inexistente é pior do que não ter instrução.
- **Passo 1 não dizia que a conta precisa ser pública.** Conta de Criador pode
  ser privada, e nesse caso a Meta não envia notificação de comentário: nada
  dispara e não dá erro.
- **Abas de Configurações estouravam em scroll horizontal.** A palavra
  "Integração" se repetia em três abas sem informar nada; saiu. Em 1366px as
  seis abas cabem em 768px, sem rolagem.
- **Automação já ativa não avisava quando o webhook caía** — seguia mostrando
  "Ativa" sem disparar nada. O selo "Aguardando conexão" só aparecia em
  automação pausada; agora vale para qualquer status.

### Notas de teste

- **Botão na private reply:** texto puro e template com botão falham com o
  **mesmo** erro (code 100 / subcode 2534014) para `comment_id` inválido — o
  template passou na validação de formato. Não é prova definitiva: exige queimar
  uma private reply real, já que a Meta permite uma por comentário, para sempre.
- **Trava de `webhook_subscrito`:** setar `false` no banco não exercita a
  recusa — `_exigir_webhook_ativo` chama `garantir_webhook()`, a conta é
  re-inscrita na hora e a ativação passa. Para ver a recusa, a re-inscrição
  precisa falhar de verdade.

## [Não versionado] - 2026-08-20 (Painel Admin — Rodada 8)

Pedido do Luiz (15/08): 4 itens, três deles pendências da Rodada 7 (o bruto do
MRR na 5ª aparição). Diferente da Rodada 7, a validação rodou contra
**produção** (read-only, via `.env.backup-1208`) — era por isso que os aceites
nunca fechavam. Os três itens de código batem **exatamente** com os números do
relatório; o item 3 foi executado em produção e mudou de diagnóstico (ver
abaixo) — o mecanismo passa no teste pedido, mas a contagem infla por reload
de página, não por refresh de token.

### Fixed

- **Denominador do churn: 6/41 → 6/20 (30,0%).** `churn_for_month()` montava o
  retrato do início do mês a partir do ÚLTIMO evento de cada assinante —
  incluindo os recebidos **depois** do corte. Duas distorções opostas: quem
  assinou em agosto entrava na base de julho (tinha `access_until` no futuro) e
  quem cancelou em agosto saía dela, que é exatamente quem o churn mede. Agora a
  base é reconstruída das cobranças no instante 31/07 23:59:59 BRT
  (`assinaturas_pagas_em()`, extraída de `mrr_at()` e usada pelos dois), somada
  às **atrasadas** — assinatura vencida mas não cancelada ainda pode churnar, e
  duas delas apareciam no numerador sem estar no denominador — e subtraída de
  quem **já estava cancelado no corte**. A contagem passou a ser por **pessoa**,
  não por chave de assinatura.
- **Cancelamento marcado como troca de plano deixa a pessoa viva para sempre.**
  `cancel_instants()` ignora `is_plan_change=True` de propósito (upgrade não é
  churn), mas a Kiwify marca esse flag em cancelamento que não é troca nenhuma:
  caso real de 31/07 — pagou 22:05, cancelou 22:07, mesmo plano dos dois lados,
  os quatro eventos marcados como troca. Essa pessoa inflava a base do churn em
  1. O churn agora olha o estado real no corte (`ultimo_evento_ate()`) em vez de
  confiar só no flag. Mesmo defeito que já tinha derrubado `new_subscriptions()`.
- **Bruto do MRR: R$1.897,42 → R$1.841,50.** Duas causas independentes:
  1. **O Max não existia na tabela de preços.** `list_price_cents()` convertia
     `max → pro`, cobrando R$67 por um plano de R$97. As três frequências do Max
     entraram em `PLAN_LIST_PRICE_CENTS` com os mesmos valores de
     `CHECKOUT_LINKS` (97 / 207 / 627).
  2. **Assinante cancelada contava duas vezes.** O import histórico da Kiwify não
     trouxe `subscription_id` (chave `cpf:`) e o webhook de cancelamento trouxe
     (chave `sub:`), então o cancelamento **não alcançava** a linha do import,
     que ficava congelada em "ativo" — cancelou em 15/08 e seguiu somando
     R$49/mês. `_latest_by_subscriber()` passou a consolidar import × webhook da
     mesma pessoa. Duas chaves `sub:` (upgrade) continuam separadas, com teste
     de regressão para isso.
- **Ponto do mês corrente do gráfico MRR ≠ card.** O mês corrente já usava
  "agora", mas por outro método: `mrr_at()` mede vigência paga e tira o bruto da
  cobrança real, enquanto o card usa a base "renovando" e o preço de tabela.
  Dois métodos, dois números na mesma tela. Enquanto o mês não fecha, o ponto
  agora **é** `mrr_cents()` — o mesmo número do card. Ao virar o mês ele passa a
  ser calculado por `mrr_at(fim_do_mes)`, e essa mudança de valor é intencional.

### Added

- `scripts/diagnostico_rodada8.py` e `scripts/validar_rodada8.py`, ambos
  read-only e com `ENV_FILE` parametrizável (`ENV_FILE=.env.backup-1208` roda
  contra produção sem tocar no `.env`). Os dois imprimem **em qual banco estão
  conectados** logo na primeira linha — a Rodada 7 fechou itens contra
  homologação achando que eram de produção. O validador ainda lista a base do
  churn nominalmente, para conferência na Kiwify.

### Item 3 — teste da 1 hora: EXECUTADO em produção (20/08)

Rodado na conta do Luiz (`user_id=9`), contra o app de produção, com o beacon e
os refreshes de token instrumentados.

**O teste pedido passa.** 65 minutos de uso contínuo, navegando pelo menu sem
recarregar e sem deslogar → **nenhum registro novo** além do login. O refresh de
token do Supabase ocorreu durante a sessão e **não** gerou acesso — a hipótese
do relatório (gravação pegando refresh) está descartada.

**Mas a contagem segue inflada, por outra causa: cada recarregamento de página
conta como um acesso novo.** Teste controlado: login + 2 reloads espaçados de
3 min = **3 registros** (16:53:06, 16:56:08, 16:59:26). No reload o `supabase-js`
reemite `SIGNED_IN` — o `AccessBeacon` só ignora `TOKEN_REFRESHED` e
`INITIAL_SESSION`, e o comentário no código assume que `INITIAL_SESSION` cobre o
reload, o que não se confirma na versão em uso.

Bate com o histórico: dos 261 acessos do Luiz, **51% têm intervalo de 2 a 10
minutos** e apenas **1%** cai na janela de 45–70 min onde um refresh apareceria.
E não é só ele — há alunas com 337, 334 e 331 registros.

**Pendente de decisão:** o que "acesso" deve significar. Se for sessão de uso e
não carregamento de página, o caminho é o beacon só disparar quando o
`access_token` muda de verdade (autenticação nova), em vez de a cada
`SIGNED_IN`; a alternativa barata é alargar a janela de dedupe de 2 min.

## [Não versionado] - 2026-08-20 (Sync restrito em homologação + menu numa linha)

### Added

- **Só o Luiz Fernando sincroniza em homologação.**
  `app/core/sync_gate.py` decide quem pode disparar sync; `app/core/ambiente.py`
  identifica o ambiente pela **ref do projeto Supabase** dentro do
  `DATABASE_URL` — nunca por `ENVIRONMENT`, que reporta a mesma coisa nos dois
  ambientes. O gate cobre os **quatro** caminhos de sincronização: botão
  Shopee (`POST /shopee/sync`), botão Facebook (`POST /facebook/sync`) e os
  dois crons (`run_shopee_sync_all` / `run_facebook_sync_all`, mais os fan-outs
  Celery equivalentes) — sem o cron, o gate dos botões não seguraria nada,
  porque ele varre todas as contas ativas sozinho todo dia. Quem não está na
  lista recebe **403** com mensagem em português. Em produção e em dev local a
  função libera todo mundo, e nem a query de e-mails do cron chega a rodar.
  **Consequência prática:** a conta `relacionamento@` (baseline de validação em
  hml) deixa de sincronizar por lá.

### Fixed

- **11 testes do Instagram nunca rodaram.** `test_instagram_automation_service.py`
  tinha 14 testes `async`, mas só 3 com `@pytest.mark.asyncio`. Com
  `pytest-asyncio` em modo `strict` (o projeto não tem `pytest.ini`, então vale
  o default), os outros 11 falhavam com "async def functions are not natively
  supported" — passavam despercebidos no meio da suíte. Marcadores adicionados;
  os 11 rodam e passam. Os outros 8 arquivos com teste async já marcavam 100%.
- **"Automação Instagram" quebrava em duas linhas no menu.** O rótulo pedia
  168px e o item só tinha 144px. `whitespace-nowrap` resolveu a quebra mas o
  texto passou a invadir o ícone de cadeado, então a sidebar foi de `w-64` para
  `w-72` — nenhuma fonte diminuiu e agora sobram 20px entre o texto e o
  cadeado. Medido no browser, não no olho.
- **Toast de erro de sync mostrava JSON cru** (`{"detail":"..."}`). O helper
  `erroDaResposta` que já existia dentro de `instagram.service.ts` virou
  `services/http-error.ts` e passou a ser usado também pelos serviços da Shopee
  e do Facebook.
- **`test_shopee_sync_fairness` dependia do `.env` da máquina** — com o
  `DATABASE_URL` apontando para hml, o gate novo entrava e o teste falhava. Ele
  agora fixa o banco que usa.

### Notas

- `_fila_do_banco()` (Celery) passou a usar `identidade_do_banco()` em vez de
  ter a própria cópia da regex. O nome da fila **não muda** — há teste de
  regressão garantindo isso, porque fila diferente = worker escutando no vazio.

## [Não versionado] - 2026-08-19 (CI de homologação quebrado por duplicata do Finder)

### Fixed

- **Deploy para develop voltou a passar.** O commit `352b0e9` (automação de
  Instagram) levou junto 4 cópias do Finder com espaço no nome —
  `app/models/campaign 2.py`, `app/repositories/campaign_repository 2.py`,
  `app/services/campaign_service 2.py` e
  `tests/unit/test_no_login_10d_exige_user_id 2.py`. O step "Validate Python
  syntax" usa `find ... | xargs python -m py_compile`, e o `xargs` quebra o
  nome no espaço: o CI falhou com
  `[Errno 2] No such file or directory: './app/models/campaign'` (exit 123),
  sem apontar o arquivo culpado. Os 4 arquivos eram versões **defasadas** dos
  módulos reais (não importáveis por Python, já registrados como débito
  técnico) e foram apagados.
- **CI endurecido contra o mesmo erro** (`deploy-homologation.yml` e
  `deploy-production.yml`): `find -print0 | xargs -0` passa a tolerar espaço
  no nome, o exclude de `__pycache__` passou a valer em subdiretório
  (`*/__pycache__/*`, não só na raiz) e um step novo
  ("Check for duplicate/invalid Python filenames") falha **antes** do
  `py_compile` imprimindo o nome do arquivo.
- **`.gitignore` do backend bloqueia `* [0-9].py`**, para a duplicata do
  Finder/iCloud não voltar a ser commitada por um `git add -A`.

## [Não versionado] - 2026-08-13 (Painel Admin — Rodada 7: acabamento pós-rodada-6)

Pedido do Luiz (12/08): 10 itens de acabamento sobre a Rodada 6 (3 deles
reincidências de rodadas anteriores). Plano de 15 tasks + 1 task extra
(14b) executado via subagent-driven-development (implementador + revisor
por task, 3 achados Important corrigidos e reverificados em rodadas de
fix). Diferente da Rodada 6, a validação (`scripts/diagnostico_rodada7.py`
e `scripts/validar_rodada7.py`) rodou só contra **homologação** — sem
acesso a produção neste ambiente — então vários números de aceite
calibrados contra dado real de produção (12/08) não bateram ao rodar em
hml, o que é esperado (base de dados diferente/menor), exceto um achado
que se confirmou ser bug real (ver "Achado fora do escopo original"
abaixo). **Pendências que precisam de confirmação humana antes de dar a
rodada por fechada** — ver seção no fim.

### Fixed

- **Denominador do churn passa a ser quem está renovando, não toda a base
  com acesso.** `churn_for_month()` usava `active_subscribers()` (~37,
  inclui cancelado-com-acesso) como denominador; agora usa
  `renewing_subscribers()` (~20) — consistente com a redefinição de
  "ativos" da Rodada 6, que já tinha deixado esse denominador como decisão
  pendente. Card de taxa de churn muda; contagem (`churn_count`) não.
  `app/services/admin_metrics_service.py`.
- **Bruto do MRR usa preço de tabela, não a última cobrança paga.**
  `mrr_cents()` tirava o "bruto" de `amount_gross_cents` da última
  cobrança real (podia vir com desconto/cupom histórico). Decisão de
  negócio confirmada: bruto = `list_price_cents(plano, periodicidade)` já
  existente em `app/core/plans.py`, dividido pelo período, com fallback
  pro valor real só quando o plano não está no catálogo. Líquido não
  mudou.
- **Janela de período (7d/30d/90d) alinhada a dia civil BRT.**
  `PlatformUsageService._inicio()` calculava `agora - N dias` em UTC — um
  instante, não um conjunto de dias civis; ao agrupar por data, uma
  janela de "7 dias" cobria pedaços de até 8 datas diferentes ("Dias
  ativos: 8" reportado pelo Luiz). `_inicio`, `usuarias_por_dia` e
  `atividade_por_usuaria` migraram de `cast(logged_at, Date)` em SQL
  (trunca no fuso da sessão do Postgres, não BRT) pra bucketing em Python
  com `_brt_date` (mesmo helper já usado em `admin_metrics_service.py`
  pra fechamento de mês). `app/services/platform_usage_service.py`.
- **Login legado ganha a mesma janela de dedupe do fluxo principal.**
  `POST /api/v1/auth/login` (fallback pré-Supabase) gravava `UserLogin`
  direto, sem a janela de 2min que `record_access()` já tem — candidato a
  inflar contagem de acesso pra quem ainda usa esse caminho. Trocado pra
  usar `record_access()`. **Pendente:** diagnóstico contra hml não achou
  evidência de duplicação real (0 pares de login <2min) — não confirma
  nem descarta o impacto em produção; falta rodar contra produção e o
  "teste da 1 hora" pedido no brief original.
- **CSV de clientes exporta com os filtros aplicados**, não a base
  inteira. `GET /admin/clients/export.csv` ignorava `q`/`status`/`plan`/
  alertas — passou a aceitar os mesmos parâmetros de `GET /admin/clients`.
  `app/api/v1/routes/admin_panel.py`.
- **Card "Sem acesso há 10d+" abre a lista já filtrada.** O link mandava
  `?sem_acesso=N`, a lista só lia `?no_login_10d=1` — clicava no card e a
  lista abria sem filtro nenhum. `PlatformUsageTab.tsx`.
- **Label do último ponto do gráfico de MRR não corta mais** — `LineChart`/
  `BarChart` (MRR e Faturamento) sem `margin`, o SVG cortava o valor
  exposto na borda direita. `CHART_MARGIN` novo em `chart-defaults.tsx`.
- **Essencial mostra "—" em Links/Páginas em vez de "0/0".** "0/0" não
  distinguia "não usa" (Pro, problema de adoção) de "não tem" (Essencial,
  limitação de plano). `atividade_por_usuaria()` passou a expor `plan`;
  frontend usa `planLimit(plan, recurso) === 0` pra decidir.
  `PlatformUsageTab.tsx`, `AdminClientDetail.tsx`.

### Fixed — achado fora do escopo original das 10 itens

- **Card "Sem acesso 10d+" e a lista filtrada pelo mesmo critério não
  batiam** (17 vs. 26 em hml) — descoberto pelo script de validação da
  Task 14, não fazia parte do brief original. Causa: 9 assinantes
  importados do histórico Kiwify sem `user_id` vinculado (nunca criaram
  conta na plataforma) eram excluídos do card (`_base_ativa()` já
  filtrava `if ev.user_id`) mas incluídos na lista (`list_clients()`
  não tinha o mesmo filtro — "sem login → inclui"). Decisão de negócio
  confirmada: sem conta não há "acesso" a medir, não conta como "sem
  acesso". `list_clients()`'s filtro `no_login_10d` passou a exigir
  `ev.user_id` — e especificamente o campo BRUTO do evento, não uma
  variável local que pode ser resolvida por fallback de e-mail (achado
  de uma segunda rodada de revisão: um assinante sem `user_id` mas com
  e-mail batendo numa conta real reabriria o mesmo descompasso se o
  guard checasse a variável errada). `app/services/admin_metrics_service.py`.

### Changed

- **Dashboard do admin em grid 2×2.** "Novas × canceladas" e "Plano ×
  periodicidade" estavam esticados em largura total; agora dividem a
  segunda linha do grid com MRR/Faturamento na primeira.
- **Paginação na lista de Clientes** (20/página, "Mostrando X–Y de N" no
  rodapé, reaproveitando `Paginacao`/`AdminTableFooter` já usado em
  `AdminSyncStatus` — retrocompatível, sem mudar o texto lá). Busca varre
  a base inteira (client-side sobre o array já filtrado); trocar
  ordenação ou filtro de status volta pra página 1.
- **Chips de filtro de alerta nomeados e removíveis** no lugar do botão
  genérico "Limpar alerta" — cada filtro ativo (`expiring_7d`,
  `payment_failed`, `never_connected`, `no_login_10d`) vira um badge com
  label próprio e × individual, em vez de limpar todos de uma vez.

### Investigado, sem mudança de código

- **Item 1 do brief — "novas de julho deveria ser 8, mostrava 6":**
  `new_subscriptions()` já contava pela 1ª cobrança paga histórica do
  assinante, sem filtrar por status atual — exatamente o comportamento
  pedido. Confirmado contra hml: `new_subscriptions(2026, 7) = 8`, bate
  com o esperado. Nenhum fix de código necessário. **Pendente:**
  reconfirmar contra produção (o "6" original do brief veio de lá).

### Pendências (precisam de humano com acesso a produção/browser)

Neste ambiente não havia sessão de admin, browser, nem acesso a produção
— os seguintes itens do plano da Rodada 7 não puderam ser executados e
ficam como checklist antes de considerar a rodada fechada:

- [ ] Reconfirmar itens 1, 2, 3 e o achado do card/lista (seção acima)
      contra **produção** (só hml foi validado).
- [ ] Teste da 1 hora (item 4): logar, usar por 1h sem deslogar, conferir
      no banco que gerou exatamente 1 linha em `UserLogin`.
- [ ] Regressão manual: sync Shopee, sync Meta/Facebook, OAuth
      (login/logout), pausar/ativar campanha, editar orçamento.
- [ ] Checagem visual: label do MRR não corta; Dashboard em grid 2×2;
      paginação de Clientes mostra "Mostrando X–Y de N".

## [Não versionado] - 2026-08-12 (Painel Admin — Rodada 6: cobrança duplicada, MRR, upgrade)

Pedido do Luiz (11/08): cobranças duplicadas em massa no painel admin, mais
redefinição de MRR/ativos, detecção de upgrade em qualquer ordem, taxa de
renovação, e uma leva de ajustes visuais/UX. Plano de 15 tasks executado via
subagent-driven-development (implementador + revisor por task); a validação
final (Task 14) rodou contra **produção** e achou + corrigiu mais 5 bugs
reais que só apareciam com dados de verdade — nenhum deles visível nos
testes sintéticos das tasks originais.

### Fixed — cobranças duplicadas (URGENTE)

- **Cobrança única identificada por `order_ref`, não mais reconstruída do
  array `charges.completed[]`.** O import histórico injetava um array
  sintético cujo `order_id` era na verdade o `order_ref` do export; o
  webhook real usa o UUID interno da Kiwify — duas chaves pra mesma
  cobrança, faturamento e total pago dobrados (casos reais: Letícia,
  Alexandre, Bruna Alves, Bruna Cabral, Mariana — todos com valor 2x ou com
  dois valores diferentes pra mesma venda). Módulo novo
  `app/services/charges.py`: uma cobrança = um evento pago, chaveado por
  `order_ref` (fallback `order_id`); webhook sempre prevalece sobre import
  no mesmo `order_ref`. Nada é armazenado calculado, então total pago,
  faturamento e gráficos se corrigiram sozinhos — **sem DELETE** em
  produção.
- **Array `charges.completed` deixou de inserir cobrança** — vira só
  verificação (`unknown_array_charges`): se cita uma cobrança que não
  conhecemos, loga "possível webhook perdido" e não insere nada.
  Isolado em try/except próprio pra uma falha na verificação nunca poder
  derrubar o registro do evento em si.

### Changed — MRR, ativos, upgrade, renovação

- **MRR e "Assinantes ativos"** contam só quem vai renovar
  (`renewing_subscribers()`): cancelada sai no mês do cancelamento, mesmo
  com acesso vigente. `active_subscribers()` (quem TEM acesso) continua
  sendo a base da aba Uso, alertas e da lista de Clientes — só MRR/ARPU/
  breakdown de plano mudaram de base. Gráfico histórico de MRR desconta
  cancelamento, mas só dentro da vigência que está sendo somada (uma
  reassinatura depois de um cancelamento antigo não é afetada).
- **Upgrade/downgrade detectado em qualquer ordem.** Caso real: Ana Ariel
  assinou o Pro 8 minutos ANTES de a Essencial anterior ser cancelada — a
  regra antiga só olhava "cancelou antes, assinou depois" e não detectava
  o par, contando como nova assinatura E como churn no mesmo mês.
  `encontrar_par_de_plan_change` agora busca nos dois sentidos com janela
  de ±30 dias. **Achado só em produção:** a mesma bidirecionalidade,
  aplicada também ao caso de MESMO plano, suprimia churn real (renovação
  normal seguida de cancelamento de verdade horas depois, mesmo plano) —
  corrigido com guarda direcional (continuação só conta cancelou-antes-
  de-pagar; upgrade de plano diferente continua bidirecional).
  `scripts/backfill_plan_changes.py` rodado contra produção.
- **Taxa de renovação** — cancelamento no vencimento conta como
  NÃO-renovação. Denominador trocou de `next_payment` (corrompido pelo
  import histórico, que carimbou `access_until` em todo evento inclusive
  cancelados) para o fim das vigências pagas. **Achado só em produção:**
  faltava excluir quem pagou perto do vencimento mas cancelou de verdade
  horas depois no mesmo ciclo (contava como renovação); e a janela do
  cancelamento precisou ficar restrita ao próprio ciclo, não ao mês
  inteiro, senão um cancelamento não-relacionado semanas depois anulava
  retroativamente uma renovação genuína.
- **Lista de Clientes: upgrade não aparece mais duplicado.** Kiwify
  atribui `subscription_id` novo em todo upgrade, então a mesma pessoa
  virava duas linhas (plano antigo cancelado + plano novo). **Achado só em
  produção, 3 sub-rodadas de correção:** (1) sem guarda, um upgrade seguido
  de churn real do plano novo apagava a cliente inteira da lista/CSV/ficha;
  (2) o total pago fundido usava o CPF inteiro como filtro, contaminando
  uma assinatura independente da mesma pessoa que sobrevive ao lado da
  fundida; (3) a candidatura a "quem absorve o total" lia o evento mais
  recente do sobrevivente (efêmero) — bastava uma renovação normal depois
  do upgrade pra zerar a candidatura e o dinheiro do plano antigo sumir de
  vez. As três corrigidas com testes de regressão sintéticos.
- Filtro de status da lista de Clientes aceita múltiplos valores
  (`ativo,atrasado,cancelado_com_acesso`); busca por texto ignora o filtro
  ativo (achar uma cliente inativa mesmo fora do filtro padrão).

### Fixed — UI

- Colisão de célula na lista de Clientes: "Acesso até 10/09/2**R$6**81,50"
  → formato curto "até 10/09/26" só pra `cancelado_com_acesso`.
- Gráfico da aba Uso ganhou a série principal (barras de acessos por dia) —
  `usuarias_por_dia()` não devolvia `acessos`, só a linha de usuárias
  renderizava.

### Added

- Gráfico "Novas × canceladas por mês" no Dashboard admin.
- Colunas **Links** e **Páginas** (`em uso / criados`) na tabela de
  atividade por usuária e na ficha individual — leitura agregada nas
  tabelas do produto, sem tocar na estrutura que serve as alunas.
- Padrão visual único dos gráficos (Dashboard + Uso): sem gridlines, hover
  band escuro, valores expostos sem hover, tooltip escuro consistente —
  centralizado em `chart-defaults.tsx`.
- Lista de Clientes ordenável (Nome, Próx. cobrança, Total pago, Último
  acesso); abre filtrada (sem Inativo) e ordenada por Próx. cobrança
  ascendente.
- `scripts/validar_rodada6.py` — confere os números de aceite contra o
  banco; rodado 3x contra produção nesta rodada.

### Removed

- Sublinha de composição por plano ("Essencial 5 · Pro 23") do card
  "Assinantes ativos".
- Linha "N contas no total" do card de usuárias da aba Uso.

### Verificado contra produção (11-12/08/2026)

Letícia R$181,50 · Alexandre R$181,50 · Bruna Alves R$135,70 · Bruna
Cabral R$42,35 · Mariana R$94,99 · Ativos=30 · MRR=R$1.411,98 ·
ARPU=R$47,07 · Novas=14 · Churn=4 · Daniel fora do MRR com acesso até
10/10 · Ana Ariel aparece 1x (Pro, ativo) · faturamento abr-ago batendo
com o import. Taxa de renovação = 50% no corte exato que o aceite citava
("até 11/08"); o valor ao vivo passou a 66,7% só porque uma 3ª assinante
venceu e renovou durante a própria sessão de validação — comportamento
correto de métrica "ao vivo", não bug.

Deploy: `develop` (hml), backend + frontend. Pendente: checagem visual
manual dos gráficos/colunas/lista antes do deploy pra `main` (produção) —
não executável sem backend+frontend rodando juntos.

## [Não versionado] - 2026-08-11 (fix: campanha de conta desmarcada contava como ativa)

Usuária reportou (com print do app oficial do Facebook ao lado) que o card
"Orçamento/dia" da tela Campanhas mostrava **10 campanhas ativas**, mas ela
só tinha **8** no Facebook — só 1 das 2 contas de anúncio dela estava
marcada em Configurações → Facebook.

### Fixed

- **`CampaignRepository.list_by_user()` nunca filtrava por conta de anúncio
  selecionada.** Quando o usuário desmarca uma conta (checkbox, múltiplas
  contas suportadas desde a Rodada 4 de Campanhas), o sync para de tocar
  nas campanhas daquela conta — mas o `status`/`effective_status` fica
  CONGELADO no banco, nunca vira `PAUSED`. Campanha órfã contava como
  ativa pra sempre, inflando "campanhas ativas" e o orçamento/dia somado.
  Fix: `list_campaigns()` busca as contas atualmente selecionadas
  (`FacebookIntegration.account_ids_list()`) e `list_by_user()` ganhou
  parâmetro `ad_account_ids` pra restringir a elas.
  `marketdash-backend/app/repositories/campaign_repository.py`,
  `app/services/campaign_service.py`.
- **Efeito colateral bom, de graça**: a mesma fonte (`list_campaigns()`)
  alimenta o Diagnóstico IA (`ai_snapshot_service.py`) e o resumo diário do
  WhatsApp (`whatsapp_resumo_service.py`) — os dois também paravam de
  contar campanha órfã de conta desmarcada.
- Fix é só leitura (não precisa re-sync nem migration) — efeito imediato
  assim que o deploy completa. Testado com
  `tests/unit/test_campaign_repository_ad_account_filter.py` (3 casos:
  conta desmarcada não conta, sem integração nenhuma não conta, filtro do
  repositório isolado). Deploy: `develop` (hml) e `main` (produção), backend.

## [Não versionado] - 2026-08-11 (import histórico Kiwify — produção)

`scripts/import_kiwify_historico.py` rodado de verdade em produção (43
assinaturas + 49 cobranças → 92 eventos), depois do dry-run limpo e das
migrations 047/048 já aplicadas (ver entrada seguinte). Confirmado idempotente
(2ª rodada: 0 criados, 92 pulados).

### Fixed — bug real encontrado rodando contra produção (não aparecia em homologação)

- **CPF sem zero à esquerda no CSV causou 6 identidades duplicadas.** O
  export (provavelmente Excel tratando CPF como número) removeu o zero
  inicial de 6 CPFs no `import_assinaturas.csv`/`import_cobrancas.csv`
  (ex.: rubiane virou `962240028`, 9 dígitos, em vez de `00962240028`).
  Homologação nunca pegou isso por partir de um banco vazio — o problema só
  aparece quando o CPF importado precisa CASAR com um CPF já existente de um
  evento orgânico (webhook real), pra reaproveitar o `subscription_id` e
  colapsar as duas fontes na mesma pessoa via `_subscriber_key()`. Sem o
  match, a pessoa virava "2 assinantes" — um pela identidade orgânica
  (`subscription_id` real), outro pela identidade importada (CPF truncado,
  sem `subscription_id`). Afetou: Daniele Ferreira Santos, Deivit Rafael
  Ferreira Martins, Letícia Opuchkevich, Irina kasburg da Silva, Renata Rosa
  santos, rubiane de melo carvalho — 5 delas ativas, inflando o card
  "ativos"/MRR do painel admin de verdade (não só um número errado num
  relatório, o Luiz veria isso ao abrir o painel).
  **Fix**: `UPDATE subscription_events SET customer_cpf = '<cpf 11 dígitos>',
  subscription_id = '<subscription_id da identidade orgânica>' WHERE
  dedupe_key LIKE 'import:%' AND customer_email = '<email>'` — 6 updates
  cirúrgicos, só nos 2 campos de identidade, nenhum valor financeiro ou data
  alterado. João rodou direto no SQL Editor do Supabase (Bash bloqueou o
  loop de UPDATEs pelo classificador do modo automático). Confirmado depois:
  zero duplicatas restantes (query sistemática de CPF-curto vs CPF-longo).
  **Se rodar uma nova leva de import histórico no futuro, zero-padar CPF pra
  11 dígitos ANTES de resolver `subscription_id` — não confiar que o CSV
  fonte preservou o zero inicial.**

### Números finais em produção (reconciliados, não é mais "esperado X, veio Y" sem explicação)

- **34 ativos** = 29 (28 que o Luiz esperava + Deivit, que cancela ~2min
  após assinar mas com `acesso_ate_brt` futuro — já era uma divergência
  conhecida e aceita, não um bug: "cancelado com acesso" conta como ativo
  pela regra do próprio Luiz) **+ 5** assinantes orgânicos genuinamente
  novos que entraram em produção DEPOIS do corte do CSV (09/08): Ana Ariel
  Silva, Katyusci, Maisa Achadinhos, Marília de Sousa Budrin Lopes — sem
  relação nenhuma com o import, só coincidência de janela de tempo.
- **MRR líquido R$1601,10** e o faturamento mensal mais alto que o "esperado"
  em todos os 5 meses seguem a mesma explicação (a base "esperada" foi
  calibrada só para o universo fechado do CSV, não para produção com
  assinantes orgânicos adicionais).
- **Churn julho: 7** (não 6) — mesma causa do Deivit, já documentada.

### Known issues (novo, fora do escopo desta rodada — não mexi)

- **Ana Ariel Silva (`arielmontanheiro@gmail.com`) tem 2 `subscription_id`
  distintos em produção**, ambos com `has_access=true` e o mesmo
  `access_until` — conta como 2 pessoas no painel. Não tem relação com o
  import (ambos os eventos são orgânicos, nenhum com prefixo `import:`) —
  é uma duplicata pré-existente do fluxo real de webhook, achada só de
  passagem enquanto eu investigava o bug do CPF. Não corrigido — fora do
  escopo do que foi pedido, fica pra próxima rodada.

## [Não versionado] - 2026-08-11 (deploy produção + ocultar IA/WhatsApp)

Deploy de tudo que estava em `develop` para `main` nos dois repos (backend
via fast-forward puro; frontend via merge commit — `main` tinha 1 commit
exclusivo, `f6da7de`, com conteúdo já idêntico ao que já estava em `develop`,
então o merge foi sem conflito). Cobre a rodada de import histórico Kiwify +
Painel Admin (Rodada 5, ver entrada abaixo) e as rodadas anteriores
(Campanhas/Orçamento/Meus Links/plano MAX). Diagnóstico IA e WhatsApp **não
estão prontos pra produção ainda** — o código foi junto (não dá pra excluir
código de um merge de branch inteiro sem reescrever histórico), mas ficam
ocultos por ambiente.

### Added

- **`isProductionHost()`** em `marketdash-frontend/src/core/config/api.config.ts`
  — helper novo, comparação EXATA de hostname (`marketdash.com.br` /
  `www.marketdash.com.br`), não substring. O check antigo do arquivo
  (`.includes('marketdash.com.br')`, usado só num `console.error` de
  diagnóstico) classificaria homologação como produção por engano — não foi
  reaproveitado por isso. É a fonte única pra "estamos em produção?" no
  frontend; qualquer feature-flag por ambiente futura deve reusar este helper.
- Menu **"Diagnóstico IA"** some do sidebar em produção (`DashboardSidebar.tsx`
  filtra `menuItems` por `menuKey !== "diagnostico_ia"` quando `isProductionHost()`).
  Segue liberado em homologação pra continuar em teste.
- Aba **"WhatsApp"** some de Configurações em produção (`Configuracoes.tsx`) —
  o trigger da aba some da lista E o deep-link `?tab=whatsapp` deixa de
  resolver pra essa aba (cai no fallback "shopee"), fechando o acesso direto
  por URL, não só o botão visual.

### Migrations de produção — aplicadas em 2026-08-11

João passou um Personal Access Token do Supabase (`sbp_...`) no chat e pediu
pra aplicar direto. Rodei a query de verificação abaixo ANTES de tocar em
qualquer coisa (confirma o estado real, não é mais inferência por data de
commit) e apliquei via Management API (`POST /v1/projects/{ref}/database/query`
— não precisa de `DATABASE_URL`/senha do Postgres, o PAT já basta). Produção
confirmada como o projeto Supabase `iprdyorxqdiivthtcvxf` ("marketDash").

- **001-042: confirmado aplicado** (verificação real via `information_schema`,
  não só inferência).
- **043-044 (Diagnóstico IA) e 045-046 (WhatsApp resumo): confirmado NÃO
  aplicado** — correto, features ocultas (ver "Added" acima) permanecem
  assim de propósito. A 046 tem aviso explícito no próprio arquivo: agendar
  o cron em homologação E produção com o mesmo número da Evolution manda o
  resumo em duplicidade pra afiliada (o índice único de `whatsapp_envios`
  não protege disso — são bancos diferentes).
- **047 (`kiwify_plan_products`, plano MAX) e 048
  (`subscription_events.canceled_at`/`cancel_reason`): APLICADAS.**
  Confirmado depois: 3 linhas MAX na tabela, as 2 colunas novas existem em
  `subscription_events`.
- Query de verificação usada (roda no Postgres de produção; útil pra
  reconferir estado no futuro):
  ```sql
  SELECT
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='subscription_events' AND column_name='card_rejection_reason') AS m039_card_rejection_reason,
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='shopee_integrations' AND column_name='sync_paused_at') AS m040_sync_paused_at,
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='ai_diagnostics') AS m043_diagnostico_ia,
    EXISTS (SELECT 1 FROM pg_indexes WHERE indexname='ux_ai_diagnostics_gerando_por_usuario') AS m044_indice_unico_geracao,
    EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='whatsapp_optins') AS m045_whatsapp_optins,
    EXISTS (SELECT 1 FROM cron.job WHERE jobname='whatsapp-resumo-9am-brt') AS m046_cron_whatsapp,
    EXISTS (SELECT 1 FROM kiwify_plan_products WHERE plano='max') AS m047_kiwify_max,
    EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='subscription_events' AND column_name='canceled_at') AS m048_canceled_at;
  ```

## [Não versionado] - 2026-08-10 (import histórico + painel admin)

Rodada: **import histórico de assinaturas/cobranças Kiwify** (43 assinaturas +
49 cobranças, abr-ago/2026, dados reais do Luiz) e **12 correções no Painel
Admin** (Rodada 5). Import validado ao vivo contra homologação (Postgres
real, banco vazio) — as 5 faturamentos mensais batem exatos com os números
de aceite do Luiz.

### Fixed — bugs de valor (pré-requisito do import, itens 1 e 2 da Rodada 5)

- **`my_commission` vs `amount` ambíguo.** `extract_paid_charges_union()`
  processava `charges_completed` em ordem ASC e sobrescrevia incondicionalmente
  por `order_id` — uma citação posterior de uma cobrança (sem o bloco
  `Commissions` completo, só `amount` pré-afiliado) podia substituir um valor
  correto já visto. Agora uma fonte "forte" (com `Commissions.my_commission`
  explícito) nunca é sobrescrita por uma fonte "fraca" do mesmo `order_id`.
  `marketdash-backend/app/services/admin_metrics_service.py`.
- **MRR bruto não fechava com a tabela de preços.** `mrr_cents()`/`mrr_at()`
  faziam divisão inteira (`// div`) por assinante antes de somar, perdendo
  centavos a cada um. Agora soma com precisão cheia (float), arredonda só o
  total.
- **Bucketing por mês em UTC, não BRT.** `_month_bounds()` e a extração de
  data das cobranças (`revenue_from_charges_for_month`, `_fees_from_charges_for_month`,
  `series_12m`) usavam o calendário UTC — uma cobrança de fim de mês perto da
  meia-noite (21h BRT = 00h UTC do dia seguinte) caía no mês civil errado.
  Achado rodando o import real: maio/junho divergiam em exatos R$135,70 (uma
  cobrança trimestral inteira migrada de mês). Agora todo bucketing mensal usa
  `America/Sao_Paulo`.
- **`_SUBSCRIBER_STATE_PRIORITY` sem o tipo de evento do import.** Alguém com
  uma assinatura CANCELADA antiga seguida de uma ATIVA nova sob o mesmo CPF
  tinha o evento cancelado (prioridade 2) vencendo o "latest" mesmo sendo
  cronologicamente anterior à ativa (prioridade 0, fora do dicionário) — isso
  puxava o plano/periodicidade ERRADOS pro cálculo de MRR (casos reais: Laysla,
  Cristiana). `import_subscription_active` agora entra no mesmo tier de
  `subscription_canceled`.
- **`_all_events()` sem desempate determinístico.** Dois eventos com o
  MESMO `received_at` (caso real do import — continuação, cancela e reassina
  no mesmo instante) ficavam em ordem não-determinística; `id ASC` como
  segundo critério resolve.
- **`list_clients`/`client_detail` agrupavam total pago por e-mail, não CPF.**
  Duas assinaturas da mesma pessoa com CPFs diferentes (ex.: Alexandre)
  mostravam o total pago das duas COMBINADO em cada linha. Agora segue a
  mesma prioridade de `_subscriber_key()` (subscription_id → CPF → e-mail).
  `client_detail()` também passou a escolher a assinatura mais RECENTE (por
  `started_at`) como "base" da ficha, não a primeira da lista.

### Added — mecanismos compartilhados (Parte B)

- **Migration 048**: `canceled_at`/`cancel_reason` em `subscription_events`.
- **`_mark_plan_change_if_needed` ganhou `reference_time`** — sem isso, um
  evento histórico de abril nunca dispararia a regra de continuação/upgrade
  (a janela de 30 dias era sempre contada a partir de "agora", a data de
  execução). Também passou a detectar "continuação" (mesmo plano, ≤1 dia),
  não só "upgrade" (plano diferente, ≤30 dias).
- **`churn_for_month` exclui "Cancelado pelo produtor"** (ajuste
  administrativo, não churn real) via a nova coluna `cancel_reason` — escopo
  isolado, não reusa `is_plan_change` (que já tem semântica própria).
- **Backfill de `subscription_id`** em `record_subscription_event()`: quando
  a Kiwify manda o primeiro `subscription_id` real pra um CPF com histórico
  órfão (importado ou não), adota retroativamente — sem isso, a pessoa vira
  "2 assinantes" a partir da primeira renovação real pós-import.

### Added — script de import (Parte C)

- `marketdash-backend/scripts/import_kiwify_historico.py` (novo) — idempotente
  (`--dry-run`, `--validate`), CSVs em `scripts/data/import_assinaturas.csv` e
  `import_cobrancas.csv`. Resolve `user_id`/`subscription_id` só leitura (não
  reusa `find_or_create_user`, que dispara e-mail de "defina sua senha").
  Todo evento gerado (assinatura ou cobrança) carrega o mesmo "estado atual"
  da pessoa — espelha o formato real de webhook Kiwify e evita que uma
  cobrança sem esse estado vença o "latest" por engano.
- Pares de continuação/upgrade calculados em memória (sem roundtrip ao banco),
  validados contra os 4 exemplos reais do CSV (Luiz, Cristiana, Alexandre,
  Lara).

### Fixed — Painel Admin, itens 4-11 da Rodada 5

- **Item 4 — tela Clientes**: largura cheia (sem `max-w-6xl`, só nessa rota),
  header sticky, colunas com largura fixa (`table-fixed`), zero scroll
  horizontal. Rótulo "Próx. cobrança" usava sempre `next_payment`, mesmo
  quando `cancelado_com_acesso` (onde o certo é `access_until`) — corrigido.
  CSV export humanizado no backend (nome de coluna em português, `R$ X,XX`,
  `dd/mm/aaaa`, status/plano/periodicidade traduzidos — o CSV é gerado no
  Python, não dava pra resolver só no frontend).
- **Item 5 — menu "Sincronizações" → "Uso e Sistema"**; abas internas viram
  "Uso da plataforma" / "Sistema" (Syncs + WhatsApp fundidos, com
  sub-cabeçalhos). Links antigos com `?tab=syncs`/`?tab=whatsapp` continuam
  funcionando (alias pra "sistema").
- **Item 6 — card "Usuárias ativas"**: denominador vinha de
  `subscriptions.is_active` (revalidado preguiçosamente, e inexistente pra
  quem só foi importado historicamente) — trocado por
  `AdminMetricsService.active_subscribers()`, sempre fresco. Card ganhou
  segunda linha secundária "N contas no total" (o número antigo, agora
  contexto, não mais denominador).
- **Item 7 — gráfico "Acessos e usuárias por dia"**: barras de acessos (eixo
  principal) + linha de usuárias distintas (eixo secundário), 2 eixos Y.
- **Item 8 — tooltip claro**: `PlatformUsageTab` não usava o `AdminChartTooltip`
  compartilhado — trocado (`AdminChartTooltip` ganhou suporte a
  `labelFormatter`, aditivo, não muda os outros usos).
- **Item 9 — `/dashboard/settings` duplicava "Configurações" no ranking** —
  adicionado ao mapa `NOMES_DE_TELA`.
- **Item 10 — classificador de erros sem `re.IGNORECASE`**: mensagens de
  exceção variam capitalização ("Error" vs "error"), caindo em "Outros" por
  acidente.
- **Item 11 — `user_logins.ip`/`user_agent` nunca eram preenchidos** (colunas
  existiam, mas ninguém gravava) — sem isso é impossível investigar um
  outlier de acessos (várias sessões reais vs. bug de sessão reautenticando).
  Confirmado que "refresh de token conta como acesso" NÃO se sustenta no
  código (`AccessBeacon.tsx` já ignora `TOKEN_REFRESHED`).

### Known issues / debt técnico

- **Item 3 (denominador do churn histórico)**: `churn_for_month`'s
  `active_subscribers(as_of=...)` usa "evento mais recente por assinante" e
  só testa se `access_until` cobre a data pedida — não reconstrói o histórico
  ponto-a-ponto como `build_coverage_periods` faz pro MRR. Neste dataset não
  morde (nenhum cancelamento tem corte abrupto de acesso), mas quebraria se
  algum assinante tiver `has_access=False` abrupto (reembolso/chargeback) num
  mês passado mascarado por um evento mais recente. Não estendido agora —
  sem necessidade comprovada pros aceites atuais.
- **Divergência nos números de aceite do Luiz** (não é bug do código, é
  inconsistência nos números-alvo que ele mandou): rodando o import contra
  homologação, "Assinantes ativos" deu **29** (não 28) e "Churn julho" deu
  **7** (não 6) — em ambos os casos, a diferença é o **Deivit** (cancelou em
  31/07, ~2min após assinar, `acesso_ate_brt` no futuro) — que bate 100% com
  a regra que o próprio Luiz documentou ("Deivit e Daniel entram como
  'Cancelado c/ acesso' direto do arquivo") mas parece ter ficado de fora da
  contagem manual dele. **Confirmar com o Luiz antes de considerar os números
  fechados** — o sistema está aplicando a regra dele à risca; o que diverge é
  a conta feita à mão.
- **Diagnóstico do `my_commission` (item 1) não confirmado em produção** —
  o `.env` local só tem acesso a homologação (vazia). A hipótese (citação
  cumulativa de cobrança sem `Commissions` completo sobrescrevendo um valor
  correto) explica exatamente a divergência relatada (R$58,86 = as 2
  comissões de afiliado de agosto), mas vale rodar a query de diagnóstico
  (documentada no plano) contra produção antes de considerar 100% causa raiz.
- `list_price_cents("max", ...)` continua rebaixando pra "pro" — inalterado,
  fora de escopo desta rodada.

### Pendências de execução (ação humana necessária)

1. Rodar a query de diagnóstico do `my_commission` contra **produção** (não
   tenho acesso — `.env` local aponta só pra homologação).
2. Aplicar a migration 048 em produção (`ALTER TABLE subscription_events ADD
   COLUMN canceled_at/cancel_reason` — aditiva, baixo risco).
3. Rodar `scripts/import_kiwify_historico.py --dry-run` contra produção,
   revisar o output, e só então rodar de verdade — **requer autorização
   explícita**, é escrita em produção com dado financeiro real.
4. Confirmar com o Luiz a divergência Deivit/Daniel antes de fechar os
   números como "corretos".

### Arquivos tocados

- `marketdash-backend/app/services/admin_metrics_service.py` (bugs de valor,
  BRT, prioridade, desempate, CPF)
- `marketdash-backend/app/services/subscription_event_recorder.py`
  (`reference_time`, backfill de `subscription_id`)
- `marketdash-backend/app/services/platform_usage_service.py` (itens 6, 9)
- `marketdash-backend/app/services/daily_access_service.py` (item 11)
- `marketdash-backend/app/services/sync_monitoring_service.py` (item 10)
- `marketdash-backend/app/api/v1/routes/admin_panel.py` (CSV humanizado,
  captura ip/user_agent)
- `marketdash-backend/app/models/subscription_event.py`,
  `marketdash-backend/migrations/048_subscription_events_cancel_details.sql` (novo)
- `marketdash-backend/scripts/import_kiwify_historico.py` (novo),
  `scripts/data/import_assinaturas.csv`, `scripts/data/import_cobrancas.csv` (novos)
- `marketdash-frontend/src/features/admin/pages/AdminClients.tsx`,
  `components/AdminLayout.tsx`, `lib/format.ts` (item 4)
- `marketdash-frontend/src/features/admin/pages/AdminSyncStatus.tsx` (item 5)
- `marketdash-frontend/src/features/admin/components/PlatformUsageTab.tsx`,
  `AdminChartTooltip.tsx` (itens 6, 7, 8)
- `marketdash-frontend/src/services/admin-panel.service.ts` (tipos)
- Testes novos: `test_charges_union.py`, `test_admin_metrics_service.py`,
  `test_admin_metrics_service_churn.py` (novo), `test_subscription_event_recorder.py`,
  `test_import_kiwify_historico.py` (novo), `test_platform_usage.py`,
  `test_platform_usage_base_ativa.py` (novo), `test_daily_access.py`

## [Não versionado] - 2026-08-10 (noite)

Rodada: **Diagnóstico IA** (endurecimento pré-merge) e **Resumo diário no
WhatsApp** — feature nova, no ar em homologação.

### Added

- **Resumo diário no WhatsApp.** Às 9h de Brasília, a afiliada Pro/Max que optou
  por receber ganha os números do dia anterior e um aviso quando alguma campanha
  fica abaixo do ponto de equilíbrio. Sai do número do MarketDash — a afiliada
  não conecta o WhatsApp dela em lugar nenhum. Provedor: Evolution API
  auto-hospedada no Coolify.
  - Os números vêm do `KpiService`, o mesmo da tela e do Diagnóstico IA. Nenhuma
    conta é refeita: resumo que contradiz o dashboard destrói a confiança no
    produto inteiro.
  - **Anti-banimento**, em ordem de importância: opt-in duplo (código no WhatsApp
    digitado de volta no site); SAIR pelo próprio WhatsApp via webhook,
    desligando todas as contas que usam aquele número; intervalo aleatório entre
    mensagens; teto diário; e disjuntor que para o lote em falhas seguidas.
  - Idempotência é do banco: índice único `(user_id, tipo, dia)`. Cron rodando
    duas vezes não reenvia. Número que a Evolution recusa desliga só aquela
    afiliada, sem derrubar o lote.
  - Agendamento por pg_cron + pg_net chamando a API, como o sync da Shopee.
  - Backend: `services/{evolution_client,whatsapp_optin_service,
    whatsapp_resumo_service,whatsapp_envio_service,whatsapp_runner}.py`,
    `repositories/whatsapp_repository.py`, `models/whatsapp.py`,
    `api/v1/routes/whatsapp.py`, migrations 045 e 046.
  - Frontend: aba WhatsApp em Configurações (cadastrar/confirmar/desligar) e aba
    no Admin → Sincronizações com estado do número e QR.
  - Docs: `docs/whatsapp-evolution.md` (procedimento verificado) e
    `docs/superpowers/specs/2026-08-07-resumo-whatsapp-design.md`.

- **`KpiService` com cobertura de SQL real** (SQLite em memória, 20 casos):
  dedupe de pedido, filtro de status, recorte por usuária/período, impostos,
  ROAS sem divisão por zero e normalização de sub_id. Antes só existia o teste de
  paridade rodado à mão.

- **Redeploy manual de homologação** (`workflow_dispatch` em
  `deploy-homologation.yml`). Mudança de variável no Coolify não reinicia o
  container, e sem isso a única forma de aplicar era um commit vazio.

### Fixed

- **WhatsApp — o SAIR não chegava.** `request.url_for()` monta a URL com o
  esquema que chega no container (http, porque quem termina o TLS é o proxy). A
  Evolution recebia `http://api.hml…`, que responde 301, e ela não segue
  redirecionamento: a afiliada respondia SAIR e nada acontecia. Pior classe de
  falha desta feature — silenciosa, e a próxima notícia seria uma denúncia
  derrubando o número. Agora `X-Forwarded-Proto` define o esquema, e a tela do
  admin **reconcilia** o webhook a cada abertura, não só na criação.

- **Diagnóstico IA — duas gerações simultâneas debitavam 20 créditos.**
  `em_andamento()` checava e só depois inseria; entre as duas coisas cabe outra
  requisição. Quem fecha é o banco: índice único parcial em `(user_id) WHERE
  status='gerando'` (migration 044). Medido contra hml com requisições
  concorrentes: `[201, 409]` e 10 créditos.

- **Diagnóstico IA — JSON válido não é relatório.** Um objeto sem seções era
  cobrado e renderizava tela quase em branco (a tela tem blindagem contra campo
  faltando, então falhava calada). `validar_relatorio` exige resumo e ao menos
  uma seção com conteúdo, **antes** do débito.

- **Diagnóstico IA — sessão presa em "gerando"** (processo morto no meio)
  bloqueava a usuária num 409 permanente. Expira em 15 minutos.

- **Diagnóstico IA — ordem do chat** desempata por `id`: pergunta e resposta
  entram no mesmo commit e podem dividir o timestamp, invertendo a conversa.

- **Diagnóstico IA — instrução do prompt vazava para a tela.** A regra do campo
  estava escrita como se fosse o valor no modelo JSON, e a IA copiou o texto
  literal: "Números do período" exibia *"string vazia"*. Também: enum interno em
  inglês aparecia no relatório ("classificadas como 'healthy'") e o CSS de
  impressão era global — imprimir o Dashboard saía em branco.

- **Diagnóstico IA — menu fechado por plano no frontend.** O espelho de
  `app/core/plans.py` em `plans.ts` não tinha `diagnostico_ia` (a Pro perdia o
  menu quando a chamada de contexto falhava), e a rota não tinha `RequirePlan`.

### Infra

- Evolution API provisionada em homologação (`evolution.hml.marketdash.com.br`,
  HTTPS válido). Duas armadilhas do Coolify documentadas em
  `docs/whatsapp-evolution.md`: "Connect To Predefined Network" torna o alias
  `postgres` ambíguo e a Evolution conecta no banco errado (o `P1000` mente — a
  credencial está certa); e o Coolify regenera `SERVICE_PASSWORD_*` a cada deploy
  enquanto o volume mantém a primeira senha.
- Migrations 043/044/045/046 aplicadas **apenas em homologação**. Produção e
  `main` não foram tocados nesta rodada.

## [Não versionado] - 2026-08-10

Rodada: Campanhas (pedidos cancelados, orçamento atual, bloco Anúncios × Shopee),
Meus Links (último clique) e preparação do Plano MAX.

### Fixed

- **Campanhas — pedidos cancelados não contam mais em "Pedidos".** Antes,
  `aggregate_by_subids()` e `daily_by_subid()` contavam pedidos cancelados junto
  com os válidos (total do card e dia a dia), divergindo do Dashboard, que já
  excluía. Regra replicada: a comissão da linha cancelada continua somando (a
  venda existiu), só o `order_id` não entra na contagem de pedidos — usa `COUNT
  (DISTINCT ...)` com `CASE` retornando `NULL` pra linha cancelada. Aplicado
  também a `direct_orders` ("Diretos: X·Y%"), pra não passar de 100%.
  - `marketdash-backend/app/utils/order_status.py` (novo — `STATUS_CANCELADO`
    extraído de `kpi_service.py` pra evitar repository importando de service).
  - `marketdash-backend/app/repositories/campaign_repository.py`
  - `marketdash-backend/app/services/kpi_service.py` (só o import, sem mudança de
    comportamento).
  - Teste: `tests/unit/test_campaign_repository_cancelled_orders.py` (caso real:
    Sub ID `clareadorretinal12060826`, 06/08, 8 pedidos todos cancelados).

- **Campanhas — card "Orçamento/dia" agora é um retrato 100% do agora.** Antes,
  o orçamento/campanhas-ativas era somado só sobre as campanhas com movimento
  (gasto/cliques/pedidos) no período filtrado — uma campanha ativa hoje sem
  movimento no range escolhido não entrava na soma. Agora é calculado sobre
  TODAS as campanhas ativas do usuário, antes de qualquer filtro da tela
  (busca, status, período). Tag "atual" (pontinho verde) adicionada ao card;
  label "Orç./dia" → "Orçamento/dia".
  - `marketdash-backend/app/schemas/campaign.py`
    (`CampaignKPIs.active_campaigns_count`)
  - `marketdash-backend/app/services/campaign_service.py`
    (`_compute_kpis`/`list_campaigns`)
  - `marketdash-frontend/src/shared/types/campaign.ts`,
    `marketdash-frontend/src/stores/campaignsStore.ts`
  - `marketdash-frontend/src/features/dashboard/pages/Campanhas.tsx` (`KpiCard`
    ganhou prop `tag`; removido cálculo local `activeCount`)

### Added

- **Campanhas (dia a dia) — bloco "Anúncios × Shopee".** 4 colunas agrupadas
  (fundo azulado + divisor), substituindo a antiga coluna solta "CPC": Cliques
  FB, Cliques Shopee, CPC FB, CPC Shopee. CPC FB é o CPC existente (gasto sem
  imposto ÷ cliques FB) renomeado, sem mudança de cálculo. CPC Shopee é novo:
  mesmo gasto ÷ cliques Shopee do dia (Upload Cliques, casado por Sub ID
  vinculado à campanha). Sem upload de cliques Shopee naquele dia → "—". Desktop
  (`lg:` 1024px+): sem scroll, grid ocupa a largura cheia. Mobile: mantém o
  scroll horizontal existente.
  - `marketdash-backend/app/repositories/campaign_repository.py`
    (`daily_clicks_by_subid`, nova)
  - `marketdash-backend/app/schemas/campaign.py`
    (`CampaignDailyPoint.clicks_shopee`/`.cpc_shopee`)
  - `marketdash-backend/app/services/campaign_service.py` (`get_detail`)
  - `marketdash-frontend/src/shared/types/campaign.ts`
  - `marketdash-frontend/src/features/dashboard/pages/Campanhas.tsx`
  - *(O dashboard de Cliques comparativo anúncios×shopee, em
    `DashboardClicks.tsx`, fica como está por ora — decidir depois se aposenta.)*

- **Meus Links — "Último clique" no card.** Antes só existia dentro do modal de
  insight. Agora aparece direto no card, numa linha própria, com selo "parado"
  (âmbar) quando o último clique passa de 48h sem receber clique. Data de
  criação (já existente no card) passou a ser rotulada "criado" pra não
  confundir com a nova data.
  - `marketdash-backend/app/repositories/custom_link_repository.py`
    (`get_by_user` — outerjoin com subquery agregada de `custom_link_events`,
    `last_click_at` como atributo transiente)
  - `marketdash-backend/app/schemas/custom_link.py`
    (`CustomLinkResponse.last_click_at`)
  - `marketdash-frontend/src/services/custom_link.service.ts`
  - `marketdash-frontend/src/features/dashboard/pages/CustomLinks.tsx`
  - `marketdash-frontend/src/shared/lib/utils.ts` (`formatDateTime`/
    `formatDateOnly`, extraídos de `LinkInsightModal.tsx` para reuso)
  - `marketdash-frontend/src/features/dashboard/components/LinkInsightModal.tsx`
    (passou a importar os formatters em vez de defini-los localmente)

- **Plano MAX — backend preparado (ainda FORA da página de vendas).** 3 ofertas
  Kiwify (Mensal R$97, Trimestral R$207, Anual R$627) mapeadas pro tier `max`,
  que já existia como placeholder em `plans.py`. Webhook não precisou mudar —
  já é agnóstico ao tier via `kiwify_plan_products`. Meus Links e Páginas de
  Captura passam a ser ILIMITADOS no MAX. Convenção nova: `-1` = ilimitado em
  `plan_limit()` (mantém a assinatura `-> int`, não virou `Optional[int]`).
  Liberado só por link direto da Kiwify — não aparece na página de vendas.
  - `marketdash-backend/migrations/047_kiwify_max_plan_products.sql` (novo)
  - `marketdash-backend/app/core/plans.py` (`UNLIMITED`, `is_unlimited`,
    `FEATURES["max"]["limites"]`, `CHECKOUT_LINKS`)
  - `marketdash-backend/app/services/capture_site_service.py`,
    `marketdash-backend/app/services/custom_link_service.py` (tratam
    `is_unlimited()` antes de comparar o limite numericamente)
  - `marketdash-frontend/src/shared/lib/plans.ts` (`UNLIMITED`, `isUnlimited`,
    espelho de `FEATURES.max.limites` e `CHECKOUT_LINKS`)
  - `marketdash-frontend/src/features/dashboard/pages/CustomLinks.tsx`,
    `marketdash-frontend/src/features/dashboard/pages/CapturaSite.tsx` (UI trata
    ilimitado — sem esse ajuste, um assinante MAX real veria a tela travar como
    se tivesse batido limite)
  - Testes: `tests/unit/test_kiwify_plan_lookup.py` (casos MAX),
    `tests/unit/test_plan_unlimited.py` (novo)

### Known issues (não corrigido nesta rodada)

- `campaign_repository.py` soma comissão de TODOS os status (inclusive
  "Inválido"/"Rejeitado"), diferente de `kpi_service.py`, que filtra por
  `STATUS_DO_KPI`. Divergência pré-existente entre Dashboard e Campanhas — fora
  do escopo desta rodada (instrução explícita de não mexer no cálculo de
  comissão).
- `list_price_cents("max", ...)` em `plans.py` continua rebaixando pra "pro" —
  ok enquanto MAX não estiver na página de vendas; revisar quando isso mudar
  (junto com os selos de desconto "até 29%/46%" do seletor de planos).
- `tests/unit/test_shopee_upsert_additive.py` tem 3 falhas pré-existentes
  (AppID Shopee inválido no fixture de teste), não relacionadas a esta rodada —
  confirmado rodando a suíte antes e depois das mudanças.
