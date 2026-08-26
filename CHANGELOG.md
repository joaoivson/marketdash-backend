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
