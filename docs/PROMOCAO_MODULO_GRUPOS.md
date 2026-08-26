# Promoção do Módulo de Grupos para produção — runbook

> **Nada deste documento foi executado.** Por decisão do João (25/08/2026), o
> módulo inteiro vive só em `develop`/homologação até ser homologado. Este
> arquivo existe para que a promoção seja uma sequência conferível, e não uma
> reconstrução de memória no dia.

> ⚠️ **O documento mestre agora é [`PROMOCAO_PARA_PRODUCAO.md`](PROMOCAO_PARA_PRODUCAO.md)**,
> que cobre Grupos **e** Instagram, traz o estado do banco medido em 26/08/2026 e
> a ordem de execução consolidada. Este arquivo ficou como detalhamento do módulo
> de grupos — e foi escrito **antes da migration `067`**, que também precisa ser
> aplicada (`conexao_convites` + `blacklist_numeros.numero_mascarado`).

O risco que estrutura o runbook: **`Base.metadata.create_all()` roda no boot** e
cria toda tabela nova em produção **sem RLS**. Quem chegar primeiro define o
resultado — se o deploy subir antes das migrations, as tabelas nascem
desprotegidas e a migration vira no-op tarde demais.

---

## 1. Antes de qualquer merge — medir, não lembrar

Nota de memória sobre estado de banco apodrece. Rode **em produção**, leitura
pura, e confira que tudo dá `false`:

```sql
SELECT to_regclass('whatsapp_instancias')   IS NOT NULL AS m058,
       to_regclass('campanhas')             IS NOT NULL AS m059,
       to_regclass('roteiros')              IS NOT NULL AS m060,
       to_regclass('integracoes')           IS NOT NULL AS m062,
       to_regclass('campanha_links')        IS NOT NULL AS m063,
       to_regclass('campanha_anuncios')     IS NOT NULL AS m065,
       to_regclass('monitoramentos')        IS NOT NULL AS m066;
```

Se **alguma** vier `true`, o `create_all` já passou por lá: a tabela existe sem
RLS. Nesse caso a migration correspondente ainda deve ser aplicada (é
idempotente e é ela que liga o RLS), mas **audite o conteúdo antes** — pode
haver dado de usuária gravado sem isolamento.

## 2. Listar o que mais vai no merge

`develop` acumula trabalho além deste módulo. Antes de abrir o merge:

```bash
git log --oneline main..develop
```

"Subir o módulo de grupos" **não autoriza** subir o resto do backlog junto.
Leia a lista e decida item a item.

## 3. Aplicar as migrations — nesta ordem, ANTES do deploy

| Ordem | Migration | Observação |
|---|---|---|
| 1 | `058_whatsapp_instancias_grupos.sql` | |
| 2 | `059_campanhas_grupos.sql` | |
| 3 | `060_roteiros_templates.sql` | cria `templates_mensagem` antes das FKs |
| 4 | `062_integracoes.sql` | tem backfill a partir de `shopee_integrations` |
| 5 | `063_campanha_links_eventos.sql` | |
| 6 | `065_campanha_anuncios_leads.sql` | o UNIQUE falha se o mesmo `campaign_id` estiver em duas campanhas de grupos — cheque duplicatas antes |
| 7 | `066_monitoramentos.sql` | |
| 8 | `067_blacklist_e_conexao_externa.sql` | `conexao_convites` + `numero_mascarado` na blacklist |

**As de cron (`061` e `064`) ficam de fora desta etapa** — ver seção 5.

Conferência obrigatória depois, em produção:

```sql
SELECT c.relname, c.relrowsecurity,
       (SELECT count(*) FROM pg_policies p WHERE p.tablename = c.relname) AS policies
  FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
 WHERE n.nspname = 'public' AND c.relkind = 'r'
   AND c.relname IN ('whatsapp_instancias','whatsapp_grupos','whatsapp_grupo_instancias',
                     'campanhas','campanha_grupos','templates_mensagem','template_variacoes',
                     'roteiros','roteiro_passos','roteiro_execucoes','roteiro_mensagens',
                     'blacklist_numeros','integracoes','campanha_links','campanha_link_eventos',
                     'grupo_eventos','grupo_snapshots','campanha_anuncios',
                     'monitoramentos','monitoramento_capturas')
 ORDER BY 1;
```

Toda linha precisa de `relrowsecurity = true`. As que têm `user_id` precisam de
`policies >= 1`; as de junção/evento (`campanha_grupos`, `campanha_anuncios`,
`campanha_links`, `campanha_link_eventos`, `grupo_eventos`, `grupo_snapshots`,
`roteiro_passos`, `template_variacoes`, `whatsapp_grupo_instancias`,
`monitoramento_capturas`) ficam com `0` de propósito — o acesso é só pelo
backend, por ownership.

## 4. Infra WAHA de produção

- Subir um recurso WAHA **próprio** de produção no Coolify (engine `GOWS`), com
  volume de sessões e `WAHA_API_KEY` fixada por env.
- Envs na API de produção: `WAHA_URL` (rede interna), `WAHA_API_KEY`,
  `WAHA_SESSAO_RESUMO`, `WAHA_WEBHOOK_TOKEN`, `WAHA_WEBHOOK_URL`.
- **`WAHA_WEBHOOK_URL` é montada da base pública configurada, nunca de
  `url_for`**: atrás do proxy o `url_for` gera `http`, toma 301 e o webhook
  falha em silêncio.
- Se produção e homologação dividirem o mesmo servidor WAHA, o prefixo de
  sessão (`mkd{ref4}`) é o que impede um ambiente de derrubar a sessão do
  outro. Melhor não dividir.
- Medir RAM real com 2–3 sessões pareadas antes de liberar para várias alunas
  (a premissa é ~60MB/sessão no GOWS).

## 5. Os dois crons — o ponto mais perigoso

`061` (roteiros, 5min) e `064` (snapshot diário) agendam `pg_net` **no banco**.
Hoje estão em **homologação**.

**Aplicar em produção e desagendar em homologação têm que ser o mesmo ato.** Com
os dois bancos agendados contra o mesmo servidor WAHA, a afiliada recebe tudo
em duplicidade — e foi um cron rodando 24×/dia que derrubou o banco
compartilhado em 20/07.

```sql
-- em HOMOLOGAÇÃO, no mesmo momento em que produção passa a agendar:
SELECT cron.unschedule(jobid) FROM cron.job
 WHERE command LIKE '%internal/cron/roteiros%'
    OR command LIKE '%internal/cron/grupos-snapshot%';
```

## 6. Frontend — remover o gating de homologação

`isProductionHost()` esconde o módulo em **quatro** pontos, e todos precisam
cair juntos: rotas (`app-routes.tsx`), sidebar (`dashboard-menu.ts`), abas da
campanha e o bloco do Dashboard. Esquecer um deixa a usuária com menu que leva
a lugar nenhum, ou com rota alcançável por link direto sem entrada no menu.

Comparação por **igualdade exata** — nunca `.includes()`: `hml.marketdash.com.br`
contém `marketdash.com.br` como substring.

## 7. Textos legais

- **Política de privacidade**: a seção 7 já cobre o módulo, incluindo o
  monitoramento (F8). Reler antes de publicar — ela afirma que, por padrão,
  não recebemos conteúdo de mensagem, e isso depende de a sessão nascer sem o
  evento `message` (validado contra o WAHA real em 26/08).
- **Termos de uso**: revisar a parte de uso do número da afiliada e o risco de
  restrição pelo WhatsApp.

## 8. Depois do deploy — o que observar por 48h

- `/admin/sincronizacoes`: runs de `roteiro_execucao` e `whatsapp_grupos`.
- Sessão órfã no WAHA (o cron diário remove; se aparecer sempre, tem bug).
- RAM do container WAHA por sessão pareada.
- Nenhuma execução parada em `enviando` por mais de uma fatia.
- Revisar os tetos (`WHATSAPP_TETO_POR_INSTANCIA`, `whatsapp_msgs_dia`,
  `WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA`) com o número real, não com a estimativa.

## 8.1 `WHATSAPP_HASH_SALT` — opcional, mas defina mesmo assim

O pseudônimo do participante é `HMAC-SHA256(segredo, jid)` e o segredo **nunca é
vazio**: sem `WHATSAPP_HASH_SALT` ele é derivado de `SHOPEE_ENCRYPTION_KEY`. Ou
seja, funciona sem configurar nada — mas isso amarra o pseudônimo à chave da
Shopee: **rotacionar `SHOPEE_ENCRYPTION_KEY` troca todos os pseudônimos** e
quebra o casamento entrada↔saída dos eventos antigos ("entraram e ficaram" para
de bater com o histórico).

Definir `WHATSAPP_HASH_SALT` explicitamente (qualquer segredo aleatório longo)
desacopla as duas coisas. Faça isso ANTES do primeiro evento de participante em
produção — depois, mudar o valor invalida o histórico.

> Contexto: até 26/08/2026 o código fazia `sha256(jid + (salt or ""))` e seguia
> sem salt, que era o caso em todos os ambientes. Telefone tem espaço de busca
> minúsculo — medido em Python puro, 1,5 M hash/s: o espaço inteiro de celulares
> brasileiros cai em ~11 minutos. O "código irreversível" da política de
> privacidade era reversível.

## 8.2 Chaves do Supabase — as duas formas convivem

O Supabase trocou o formato das chaves: `anon`/`service_role` (JWTs longos)
deram lugar a `sb_publishable_…`/`sb_secret_…`. O código aceita **as duas** e
prefere a nova:

| Uso | Nova | Antiga (retaguarda) |
|---|---|---|
| client comum | `SUPABASE_PUBLISHABLE_KEY` | `SUPABASE_KEY` |
| admin / ignora RLS | `SUPABASE_SECRET_KEY` | `SUPABASE_SERVICE_KEY` |
| frontend | `VITE_SUPABASE_PUBLISHABLE_KEY` | `VITE_SUPABASE_ANON_KEY` |

Isso permite rotacionar sem janela de indisponibilidade: acrescente a nova,
faça o deploy, confirme, remova a antiga.

Verificado contra o projeto real em 26/08/2026: **as duas chaves autenticam**, e
um token obtido com uma é aceito pelo client criado com a outra. A validação é
`auth.get_user(token)` — chamada ao servidor do Supabase, sem decodificação
local de JWT —, então a mudança do algoritmo dos tokens para **ES256** não
exigiu nada do nosso lado. `SUPABASE_JWKS_URL` fica declarada mas não é usada:
só faria falta se algum dia validássemos o JWT localmente.

## 9. Pendências conhecidas que a promoção não resolve

- **`lead` × `offsite_conversion.fb_pixel_lead` do Meta**: usamos `max()`, que
  nunca infla o número de leads. A relação exata entre os dois `action_type`
  não está fechada na documentação pública e a conta de homologação não tem
  conversão de lead para decidir. Revalidar quando uma campanha de grupos com
  pixel rodar de verdade.
- **A suíte de testes do módulo depende do Postgres local (5434)** e **pula em
  verde** onde ele não existe — o CI não protege os invariantes que ela existe
  para proteger.
- `OPENAI_API_KEY` nova (a antiga vazou) para a geração de variações por IA.
