# Memória do time — Backend

Quatro arquivos, quatro funções distintas. **Se você não souber em qual escrever,
a resposta está aqui** — a maior causa de morte desse tipo de documentação é
os arquivos virarem cópias uns dos outros.

| Arquivo | O que é | Como se escreve | Quando ler |
|---|---|---|---|
| `CONTEXTO.md` | **Estado atual.** Onde o repo está agora: o que está pronto, o que está em voo, o que está quebrado, o que depende de terceiro. | **Sobrescreve.** A seção antiga sai, a nova entra. Não tem histórico — histórico é o DIARIO. | **Sempre, no começo da sessão.** Tem precedência sobre docs mais antigos. |
| `DIARIO.md` | **Histórico.** O que mudou, quando e por quê. Mais rico que `git log`: registra o motivo e o que ficou pendente. | **Append-only.** Entrada nova no topo. **Nunca** reescreva entrada antiga — se estava errada, corrija numa entrada nova dizendo que estava errada. | Ao investigar "por que isso está assim?" ou "quando isso quebrou?". |
| `DECISOES.md` | **Decisões, pendências e débitos técnicos** deste repo, com o motivo. | Adiciona linha; atualiza status de pendência. Decisão revogada não some — vira linha nova dizendo que revogou a anterior. | Antes de propor mudança estrutural — pode já ter sido decidido e descartado. |
| `BACKLOG.md` | **Trabalho identificado e não feito**, com causa apurada e direção. Item aqui já passou por investigação: o diagnóstico está escrito. | Um bloco por item, no modelo do próprio arquivo. Concluído sai daqui e vira entrada no `DIARIO.md`; descartado vira linha no `DECISOES.md` dizendo por quê. | Ao escolher o que fazer a seguir, e **antes de investigar** algo — pode já estar apurado. |

> **`DECISOES.md` × `BACKLOG.md`** — a confusão mais provável entre os dois.
> `DECISOES.md` guarda o que foi **decidido** (inclusive "decidimos não fazer").
> `BACKLOG.md` guarda o que está **por fazer**. Um item que sai do backlog
> normalmente deixa uma decisão para trás; a decisão fica, o item some.

Mudança **cross-stack** (a maioria aqui é) tem changelog único na raiz do
monorepo: `../../CHANGELOG.md`. A memória daqui registra o **porquê** que não
cabe no changelog.

## A regra que mantém isso vivo

A skill `orquestrador-marketdash` (§4) trata a atualização destes arquivos como
**parte da tarefa**, não como algo opcional no fim. Tarefa entregue sem isso
está pela metade: o próximo dev — ou o próximo chat — reconstrói contexto do
zero e repete erro já resolvido.

## Demanda nova entra no BACKLOG na hora

Sempre que aparecer trabalho que **não vai ser feito agora** — achado de
investigação, dívida descoberta, item adiado por risco —, ele vira bloco no
`BACKLOG.md` **no momento em que é identificado**, com causa, evidência e
direção. Deixar para depois é como o contexto se perde: quem voltar ao assunto
refaz a investigação inteira.

O teste de qualidade do item é simples: **dá para começar lendo só o bloco?**
Se falta a causa, a evidência ou a armadilha conhecida, o item está pela metade.

## O que NÃO vai aqui

- O que o código já diz (estrutura de pastas, assinatura de função) — leia o código.
- O que o `git log` já diz sozinho ("renomeei X pra Y").
- Convenção permanente de engenharia → `CLAUDE.md` / `.claude/rules/`.
- Mudança de comportamento visível ao usuário → `../../CHANGELOG.md`.
- Segredo, credencial, token, senha de banco, PAT do Supabase. **Nunca.**
  (Isto já mordeu o projeto uma vez — ver `DECISOES.md`.)
