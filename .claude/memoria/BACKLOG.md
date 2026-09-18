# Backlog — Backend

> **O que é:** trabalho **identificado e não feito**, com contexto suficiente
> para começar sem reconstruir nada. Item aqui já passou por investigação — o
> diagnóstico está escrito, não precisa ser refeito.
>
> **O que NÃO é:** lista de ideias. Se não tem causa apurada e direção, não
> entra — vira ruído e o arquivo morre, que é o risco que o `README.md` já
> aponta para esse tipo de documentação.

## Fronteira com os outros arquivos

| Se é… | Vai para |
|---|---|
| Decisão **tomada** (inclusive "decidimos NÃO fazer") | `DECISOES.md` |
| O que mudou e quando | `DIARIO.md` |
| Estado atual do repo | `CONTEXTO.md` |
| Trabalho **pendente**, com causa e direção | **este arquivo** |
| Rodada em andamento | `STATUS-<assunto>.md` |

Item concluído **sai daqui** e vira entrada no `DIARIO.md` (e `DECISOES.md`, se
houve decisão). Item descartado também sai, virando linha em `DECISOES.md`
dizendo por que foi descartado — para não ser reproposto.

## Modelo de item

```
### [ID] Título
**Estado:** aberto | em andamento | bloqueado (por quê)
**Custo/risco:** estimativa honesta

**O que é.** Uma frase.
**Por que existe.** Causa apurada + evidência (número, log, medição).
**Direção.** Como atacar, na ordem.
**Armadilhas.** O que vai morder quem pegar isso.
**Pronto quando.** Critério verificável.
```

---

## Abertos

### [B-01] `DATABASE_URL` de produção não usa o pooler
**Estado:** aberto
**Custo/risco:** meio dia · risco ALTO se feito direto em produção

**O que é.** Produção conecta no Postgres pela conexão **direta**
(`db.<ref>.supabase.co:5432`), não pelo pooler. Confirmado no painel do Coolify
em 18/09/2026.

**Por que existe.** O item estava no checklist do hotfix como "conferir, não
assumir", e a conferência mostrou que está errado. Importa porque a conexão
direta tem teto de conexões atrelado ao compute, e o incidente de 17-18/09
registrou **852 × `could not accept SSL connection: EOF detected`** —
esgotamento de conexões. A API roda gunicorn com 2 workers e o Celery com
concorrência 8, cada um com pool SQLAlchemy próprio.

**Direção.**
1. Trocar em **HML** primeiro, para **modo session** (mesmo host do pooler,
   porta **5432**), validar, e só depois produção.
2. Se o objetivo for o modo **transaction** (porta 6543, que é onde está o
   ganho real de conexões), **antes** converter os advisory locks do Shopee —
   ver armadilhas.

**Armadilhas** — as três foram verificadas em 18/09, não são teóricas:

1. **`?pgbouncer=true` quebra o psycopg2.** É parâmetro do Prisma. Medido:
   `ProgrammingError: invalid dsn: invalid URI query parameter: "pgbouncer"`.
   O libpq rejeita antes de conectar — **a API não sobe**. Tem que sair da
   string de conexão.
2. **Modo transaction mata o sync do Shopee, em silêncio.**
   `shopee_integration_service.py` pega `pg_try_advisory_lock` (nível de
   **sessão**) numa conexão dedicada em AUTOCOMMIT e segura pelo sync inteiro,
   soltando com `pg_advisory_unlock` no fim (linhas ~616/678 e ~722/820). Em
   modo transaction a conexão volta ao pool a cada statement: o unlock cai em
   outro backend, devolve `false`, e o lock **vaza**. O próximo sync encontra o
   lock preso e loga `"já há um sync em andamento — ignorando (no-op)"` — para
   sempre, sem erro em lugar nenhum. O comentário do código diz que fechar a
   conexão libera o lock; com pgbouncer no meio isso deixa de valer, porque
   fechar o cliente não fecha o backend.
   O lock do Facebook usa `pg_advisory_xact_lock` e **sobrevive** — só o Shopee
   é o problema, nos dois pontos (por usuária e o global do `sync_all`).
3. **O nome de usuário muda.** Direta usa `postgres`; pooler usa
   `postgres.<ref>`. Colar a senha e esquecer isso dá erro de autenticação.

**A conferir antes de produção:** `SET LOCAL app.current_user_id`
(`dependencies.py:138`) é transaction-scoped e deve sobreviver aos dois modos,
mas **isso precisa ser testado em HML**, não descoberto em produção — se o
valor sumir, o RLS muda de comportamento e é caminho de vazamento entre contas.

**Pronto quando.** HML no pooler com RLS, sync Shopee e sync Facebook validados;
depois produção, com `/health` e um sync real conferidos.

---

### [B-02] Teto de concorrência não explicado (p95 de 16s no k6)
**Estado:** aberto
**Custo/risco:** algumas horas · risco baixo (investigação)

**O que é.** O k6 oficial (500 req/min, `maxVUs=100`) deu `p(95)=16,39s` e
3,84% de falha, e produção degradou junto (0,1s → 1,68s). Repetido com
`maxVUs=25`, a **mesma taxa** deu `p(95)=98ms`, 0% de falha e produção intacta.

**Por que existe.** A falha correlaciona com **concorrência do cliente**, não
com taxa de requisições. Com duas rodadas não dá para separar:
- **(a)** teto real de conexões no Traefik/VPS, que uma campanha grande atingiria;
- **(b)** artefato do k6 abrindo 100 sockets de um MacBook só — padrão que
  tráfego real, vindo de milhares de IPs, não reproduz.

Hipótese de **cache stampede foi testada e descartada**: 20 requisições
simultâneas com cache frio custam 0,50s (contra 27ms quente) — real, mas 30x
menor que os 16s.

**Direção.** Repetir com `maxVUs=100` por ~30s com monitoramento apertado de
produção e abort automático. Se reproduzir, é (a) e vira problema de infra
(Traefik/CPU). Se não, foi (b) e o assunto morre.

**Armadilhas.** Hml e produção **dividem o mesmo VPS**. Foi essa configuração
que levou produção a 1,68s. Não rodar sem janela e sem vigia.

**Pronto quando.** Souber se é (a) ou (b), registrado com medição.

---

### [B-03] Cache de decisão do redirect não tem proteção de stampede
**Estado:** aberto
**Custo/risco:** baixo · risco baixo

**O que é.** `custom_link_service._resolver_slug` guarda a decisão por 60s. Na
expiração, todas as requisições simultâneas daquele slug vão ao Postgres juntas.

**Por que existe.** Medido em 18/09: 20 requisições simultâneas com cache frio
custam **0,50s** cada, contra **27ms** com cache quente — 15-20x. Não foi causa
de incidente nenhum; é característica conhecida, registrada para não ser
"descoberta" de novo.

**Direção.** Se virar problema: lock curto no Redis para que só uma requisição
recarregue, ou servir o valor vencido enquanto revalida.

**Pronto quando.** Só atacar se aparecer em medição real. **Não é urgente.**

---

### [B-04] `trigger_facebook_sync` com `job startup timeout` no pg_cron
**Estado:** aberto
**Custo/risco:** meia rodada · risco médio (toca sync que já teve incidente)

**O que é.** O agendamento vive no pg_cron e registra `job startup timeout`.
Deveria ser task Celery.

**Por que existe.** Item do "depois do hotfix" do dossiê de 18/09. O pg_cron
já causou incidente próprio (24x/dia derrubando banco compartilhado, 20/07).

**Direção.** Mover para Celery, com a fila derivada do banco
(`_fila_do_banco`), como o resto.

**Armadilhas.** Prioridade de fila: usar **0** (interativo) ou **9** (batch),
nunca o default 5 — task com priority 5 cai em fila que ninguém consome e é
aceita sem nunca executar, em silêncio.

**Pronto quando.** Sync rodando por Celery, pg_cron desagendado, um ciclo
completo observado.

---

### [B-05] Front reenvia token expirado
**Estado:** aberto
**Custo/risco:** meia rodada · risco MÉDIO (mexe no caminho de login)

**O que é.** 301 × `GET /auth/v1/user` devolvendo 403 em 24h: o cliente
reenvia token já expirado em vez de renovar.

**Por que existe.** Levantado nos logs do incidente de 18/09. Some parte do
ruído no Auth e melhora a experiência de quem fica com a aba aberta.

**Direção.** Revisar o refresh no cliente (`api.config.ts` e o SDK do Supabase).

**Armadilhas.** É o caminho de login. Errar aqui desloga todo mundo. Não subir
no fim de um dia de incidente — foi a razão de ter ficado de fora em 18/09.

**Pronto quando.** Os 403 de token expirado sumirem dos logs de Auth.

---

### [B-06] Migration 087 não registrada em produção
**Estado:** aberto
**Custo/risco:** minutos · risco baixo

**O que é.** `087_colunas_facebook_pixel_e_ad_accounts.sql` existe no repo mas
não foi aplicada em produção.

**Por que existe.** As colunas **já existem** lá (o `create_all` do startup as
criou meses atrás), então a migration é efetivamente no-op — é o histórico de
schema que fica incoerente.

**Direção.** Aplicar pelo caminho normal de migration. O SQL é `IF NOT EXISTS`.

**Armadilhas.** Regra do João: **não rodar migration à mão**. Vai pelo processo.

**Pronto quando.** Registrada, com `STATUS-migrations` refletindo.

---

### [B-07] Redis órfão `y4so0kk48sg8woskskok8owo`
**Estado:** bloqueado — exige confirmação explícita do João
**Custo/risco:** minutos · risco ALTO (destrutivo, infra compartilhada)

**O que é.** Existem duas instâncias de Redis no Coolify. Só a
`h0cw0gc8owws004480g0sog8` é usada (API prod, worker prod, API hml). A segunda
não tem referência conhecida.

**Por que existe.** Achado da etapa 16b do painel de infra. Custa recurso e
confunde diagnóstico.

**Direção.** Confirmar que nada aponta para ela, **depois** apagar.

**Armadilhas.** Ação destrutiva em infra compartilhada. **Sozinha, nunca junto
de outra rodada.** O status do Coolify oscila e não serve de prova — cruzar com
`/health` real.

**Pronto quando.** Apagada, com os 4 serviços confirmados no ar.

---

### [B-08] Alertas do Supabase (IO budget, RAM, conexões)
**Estado:** aberto — ação do João no painel
**Custo/risco:** minutos · risco zero

**O que é.** Não há alerta configurado para os sinais que precederam as duas
quedas.

**Por que existe.** Em 17 e 18/09 o esgotamento de IO só foi descoberto pelo
Advisor, **depois** da queda. O aviso existia antes: *"about to deplete its Disk
IO Budget"*.

**Direção.** Configurar alertas no painel do Supabase para IO budget, RAM e
conexões, nos dois projetos.

**Pronto quando.** Alerta chegando num canal que alguém lê.

---

### [B-09] Medir uma semana em compute Micro antes de decidir Small
**Estado:** aberto — esperando tempo passar (a partir de 18/09/2026)
**Custo/risco:** zero · é observação

**O que é.** O compute subiu de nano para **Micro** em 18/09 como mitigação.
Decidir Small **com dado**, não com susto.

**Por que existe.** O hotfix mudou o perfil de carga (cliques deixaram de
escrever no Postgres por requisição). Medir antes disso seria medir outro
sistema.

**Direção.** Uma semana de IO, RAM e conexões em produção; comparar com
`docs/PLANO_ESCALA_100_USUARIAS.md`.

**Pronto quando.** Decisão registrada em `DECISOES.md`, com os números.

---

### [B-10] Dois cherry-picks a resolver no próximo merge `develop` → `main`
**Estado:** aberto — dívida programada
**Custo/risco:** minutos · risco baixo se lembrado, chato se esquecido

**O que é.** Dois arquivos de workflow foram para `main` por cherry-pick em
18/09, então têm SHAs diferentes nas duas pontas e vão reconflitar.

| Arquivo | `develop` | `main` |
|---|---|---|
| `.github/workflows/vigia-gate-aprovacao.yml` | `8341926` + `03d826b` | `0072ffa` |
| `.github/workflows/monitor-producao.yml` | `84d3993` | `f27d982` |

**Direção.** No conflito, **manter o lado da `develop`** nos dois. O conteúdo
era idêntico nas duas pontas em 18/09.

**Pronto quando.** Merge de promoção feito sem perder nenhum dos dois.

---

## Concluídos recentemente

Ficam aqui uma rodada, para quem voltar entender o que mudou, depois saem.

| Item | Quando | Onde ficou registrado |
|---|---|---|
| Hotfix login/Supabase IO em HML e produção | 18/09/2026 | `STATUS-hotfix-login-supabase-hml.md`, `CHANGELOG.md` |
| Vigia do gate de aprovação | 18/09/2026 | idem — `0072ffa` na `main` |
| Sonda sintética de login | 18/09/2026 | idem — `f27d982` na `main` |
| `ALTER COLUMN` sem guarda no `_apply_safe_migrations` | 18/09/2026 | `959e40f` |
| `SUPABASE_JWT_SECRET` em produção | 18/09/2026 | **não é necessário** — os 2 projetos já são ES256 |
