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
