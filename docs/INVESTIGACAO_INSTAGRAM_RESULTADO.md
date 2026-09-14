# Resultado da investigação — Automação Instagram não dispara

**Data:** 14/09/2026
**Responde ao:** relatório de investigação de 13/09/2026
**Status:** **causa identificada e corrigida.** Correção em produção desde 14/09.

---

## Veredito em uma linha

**A Meta entrega os eventos. O backend os descarta porque o post não tem
automação.** Não é bug de plataforma, de permissão, de matching, de dedup nem de
assinatura.

---

## O número que decidiu

O log cru de entrada pedido no §5.1 do relatório **já estava em produção desde
11/09** — é a migration `083` (`instagram_webhook_entregas`), que grava toda
chamada ao webhook **antes** de qualquer lógica de negócio, inclusive antes da
validação de assinatura.

```
Eventos de comentário recebidos (11/09 21:21 → 14/09 02:24 UTC)

  185  chegaram ao servidor
    4  responderam ........................................   2%
  146  descartados: "nenhuma automação cobre este post"  ... 79%   (26 posts distintos)
   35  descartados: comentário da própria conta ...........  19%   (correto — são as respostas dela)
```

E a perda estava **acelerando**:

| dia (BRT) | respondidos | perdidos por falta de automação |
|---|---|---|
| 11/09 | 0 | 17 |
| 12/09 | 1 | 51 |
| 13/09 | 3 | **78** |

### Como reproduzir

```sql
-- quanto chegou e o que aconteceu com cada um
select tipo, desfecho, coalesce(detalhe,'(sem detalhe)') as motivo, count(*)
from instagram_webhook_entregas
where ig_user_id is not null
group by 1,2,3 order by 4 desc;

-- por dia
select to_char(recebido_em at time zone 'America/Sao_Paulo','YYYY-MM-DD') as dia_brt,
       count(*) filter (where desfecho='enviado')                              as respondidos,
       count(*) filter (where detalhe='nenhuma automação cobre este post')     as perdidos
from instagram_webhook_entregas
where tipo='comentario'
group by 1 order by 1;
```

---

## §4 — As hipóteses do relatório

| Hipótese | Veredito | Evidência |
|---|---|---|
| **H1** — A Meta não entrega (nível de acesso) | ❌ **Refutada** | 185 eventos de `comments` chegaram em 2,5 dias. Advanced Access funciona |
| **H2** — Os eventos chegam e o backend descarta | ✅ **Confirmada**, com causa diferente da suposta | Não é matching, dedup, assinatura nem exceção engolida: **146 dos 181 descartes são "nenhuma automação cobre este post"** |
| **H3** — Entrega parcial / throttle | ❌ **Refutada** | Mesmo número; 78 eventos só em 13/09 |

---

## §6 — O critério de decisão

| Cenário previsto no relatório | Resultado |
|---|---|
| Chegaram ~3 → problema da Meta | — |
| **Chegaram ~38 → bug nosso** | ✅ **Chegaram 185.** Caiu neste cenário, com folga |
| Intermediário → throttle | — |

**A desconfiança do §1 estava certa:** o contador "2 comentários capturados" é
**pós-matching**. `contadores_por_automacao` filtra `automation_id IS NOT NULL`,
e essa coluna só é preenchida quando alguma automação casou. O número nunca
disse nada sobre entrega de webhook.

---

## §5 — As ações pedidas

| # | Ação | Estado |
|---|---|---|
| 5.1 | Log cru de entrada — **prioridade máxima** | ✅ **Já no ar desde 11/09** (migration 083) |
| 5.2 | Verificar logs existentes | ⚠️ **Impossível** — endpoint de logs do Coolify não responde (`code=000` em 75 s, duas tentativas). Não bloqueou: o ledger respondeu |
| 5.3 | Onde o contador é incrementado | ✅ **Pós-matching**, confirmado no código |
| 5.4 | Assinatura pela API, não pelo painel | ✅ `subscribed_fields: ["comments","messages"]`, conta `BUSINESS`. **Painel e API concordam** |
| 5.5 | Botão "Teste" no painel da Meta | ❌ **Não feito, e ficou desnecessário** — só provaria que a URL responde, e 185 eventos reais já provam |

---

## §2 — O que o relatório descartou por raciocínio

| Descarte | Confirmado? |
|---|---|
| Conta privada · assinatura caída · endpoint fora do ar · regras de negócio da Meta · dispositivo do comentarista · seguir ou não a conta · Reels não suportado | ✅ **Todos corretos.** Nenhum precisou ser reaberto |
| Correlação "só quem tem cargo no app recebe" | ✅ **Enterrada.** `carolinaqtavares` (13/09 02:52) e `jocileude_sampaio` (11/09) são pessoas comuns e **receberam o direct** |

---

## A causa raiz — mais específica que "faltam automações"

**Ela republica o mesmo produto e a automação fica presa na cópia antiga.**

Existem **dois posts pedindo ALGODÃO** e **dois pedindo AXILIA**. Nos dois casos
a automação está na cópia que quase não recebe comentário.

| comentários perdidos | palavra da legenda | post | automação | total de comentários no post |
|---|---|---|---|---|
| 28 | AXILIA | `/p/DbUhjTOAkGv/` | ❌ | 308 |
| 13 | boddy | `/p/DbrzZVAglVV/` | ❌ | 154 |
| 12 | **ALGODÃO** | `/p/Dc7cCO7AzNW/` | ❌ | 36 |
| 10 | MEIA | `/p/DcusqO9gVZk/` | ❌ | 42 |
| 7 | MAMADEIRA | `/p/DaMibuJAWtx/` | ❌ | 558 |
| 6 | Mochila | `/p/DbuPakQgEOd/` | ❌ | 338 |

**Seis posts concentram 63 dos 146 perdidos — 43%.** E **post antigo é o que
mais rende**: o MAMADEIRA é de 30/06 e continua coletando comentário.

---

## O post da automação está funcionando

Media `18118006687931396` (reel `Dc3rR4fRqBP`, automação "Calcinhas Algodão") —
tudo que chegou na janela do ledger:

| recebido (UTC) | quem | texto | desfecho |
|---|---|---|---|
| 13/09 02:52:25 | `carolinaqtavares` | Quero | **direct enviado** ✅ |
| 13/09 02:52:34 | resposta da própria conta | — | ignorado (correto) |
| 13/09 14:13:22 | `marketdashoficial` | Quero | **direct enviado** ✅ |
| 13/09 14:13:31 | resposta da própria conta | — | ignorado (correto) |

**Dois comentários de terceiros, dois directs. 100%.**

---

## O que continua sendo inferência

| Afirmação | Status |
|---|---|
| "O post do relatório é o `/p/Dc7cCO7AzNW/`" | ⚠️ **Fortemente indicado, não provado.** Mesma palavra (ALGODÃO), 36 comentários ≈ os "~42" do relatório, 12 eventos recebidos e 0 respondidos, sem automação. **Não dá para fechar pelos nomes**: a Graph API devolve `username: null` para comentarista terceiro |

O que **é** fato: o media da automação recebeu 4 eventos na janela, todos
processados. Se os 9 nomes da amostra do relatório estivessem nele, o ledger os
teria — cinco deles (11 h, 18 h, 22 h, 1 d, 1 d) são posteriores ao ledger
entrar no ar.

---

## O que foi para produção

| # | O que | Repo | SHA em `main` | Origem (`develop`) | Migration |
|---|---|---|---|---|---|
| 1 | Ledger de entrega do webhook | backend | `d3e5600` | `600557c` | **083** ✅ aplicada |
| 2 | Precedência, palavra da legenda e criação em lote | backend | `f2edbfb` | `564c162` | — |
| 3 | Tela "Cobrir publicações" | frontend | `f0eacc0` | `358361b` | — |

### O que cada um entrega

| Item | Efeito |
|---|---|
| Ledger (083) | "A Meta entregou?" virou consulta SQL. Foi ele que respondeu esta investigação |
| **Precedência** | **Bug latente corrigido.** `.all()` sem `ORDER BY` faria uma automação "qualquer post" com a palavra "quero" roubar comentários das específicas — não deterministicamente, porque a ordem vinha do Postgres. Sintoma seria "às vezes vem o link errado", que não reproduz |
| Palavra da legenda | `Comente " ALGODÃO "` → palavra-chave pré-preenchida. **260 de 283 legendas (92%), nenhuma errada** |
| `POST /automations/lote` | Até 50 automações por chamada. Pula post já coberto; reporta o que ficou de fora |
| `/media` enriquecido | `tem_automacao` + `palavra_sugerida` — é o que torna o buraco visível na tela |
| Tela em lote | `/dashboard/automacoes/em-lote`: modelo comum, e por post só palavra + link |

### Verificação em produção

Marcadores conferidos na URL real — **respondiam `False` antes do deploy**:

```
POST /instagram/automations/lote : True
media.tem_automacao              : True
media.palavra_sugerida           : True
bundle servido                   : assets/index-s-GWpX4P.js  (contém "Cobrir publicações")
```

Suítes rodadas no worktree de `main`, não na develop: **646** (ledger) e **694**
(cobertura), ambas sem falha. `tsc` em 26 erros contra baseline de 26.

---

## O que falta — e nada disso é código

| # | Item | De quem |
|---|---|---|
| 1 | Cobrir os posts descobertos, começando pelos **6 campeões** (43% da perda) | Beatriz |
| 2 | Resolver as **cópias duplicadas** — ALGODÃO e AXILIA com a automação na cópia errada. Toda republicação precisa de automação nova | Beatriz |
| 3 | Aviso de **"Pedidos de contato"** (§8 do relatório) nas respostas públicas padrão | decisão do João |
| 4 | Abrir `/dashboard/automacoes/em-lote` logado como a Beatriz antes de repassar a ela | João |

Sobre o item 4: a tela foi validada visualmente em 390 px e 1440 px, mas contra
**homologação**, com uma conta de teste de 118 posts pessoais e quase nenhuma
legenda "Comente X". Os 92% de sugestão na conta da Beatriz foram medidos **pela
API**, não vistos na tela com os dados dela. É o único ponto que ainda depende
de inferência.

---

## §8 — Achado lateral do relatório

Confirmado e **sem correção técnica possível do nosso lado**: quando o direct
funciona, ele cai em **"Pedidos de contato"** para quem não segue a conta. É
comportamento do Instagram.

Mitigações baratas, nenhuma feita:

- mencionar "olha nos seus pedidos de contato 👀" na resposta pública da automação;
- incluir o aviso no onboarding do recurso.

---

## A lição que se repetiu duas vezes

Em **11/09** e de novo em **13/09**, o post do print/relatório **não era** o post
da automação. Nas duas vezes o reel com automação respondeu 100% do que recebeu
(2/2 e 2/2), e os comentários sem resposta estavam numa cópia diferente do mesmo
produto.

**Antes de abrir investigação em "a automação não responde", casar o
`media_id`.** O campo `media_permalink` que a automação guarda resolve isso em
10 segundos:

```sql
select nome, status, media_id, media_permalink
from instagram_automations
where user_id = :user_id order by id;
```

O runbook completo de diagnóstico, na ordem que elimina causa por causa, está em
[`docs/DIAGNOSTICO_AUTOMACAO_INSTAGRAM.md`](DIAGNOSTICO_AUTOMACAO_INSTAGRAM.md).
