---
name: deploy-check
description: Checklist antes de promover develop para main no backend MarketDash — migrations, worker, crons e segredos.
---

Checklist antes de subir o backend para produção. Cada item é uma pergunta
com resposta verificada, não uma suposição.

## 1. O que vai junto

```bash
git log --oneline origin/main..develop
```

Leia a lista. Commit que você não reconhece **não** entra sem alguém
confirmar — este monorepo já teve backend inteiro de uma feature viajando de
carona num commit com mensagem de outra coisa.

## 2. Migrations

- Alguma migration nova entrou no diff? (`git diff --stat origin/main..develop -- migrations/`)
- Ela já foi aplicada em **homologação** e o objeto foi conferido?
- Ela faz `UPDATE` em dado real? Se sim, **reaplicar mexe em dado bom** —
  confirme se já rodou em produção antes de rodar de novo.
- Não existe tabela de controle de versão de schema: a resposta vem de
  inspecionar o objeto, não de um `migrate status`.

## 3. O worker vai junto?

O CI já deployou **só a API** uma vez e o worker Celery ficou semanas com
código velho — um `|| echo` mascarava a falha do deploy. Se o diff toca em
`app/tasks/` ou em qualquer service chamado por task, confirme que o deploy
do worker rodou e **terminou com sucesso**, não só que foi disparado.

## 4. Crons

Mudou assinatura de endpoint em `app/api/v1/routes/internal.py`? O
`pg_cron`/`pg_net` do Supabase chama essas URLs — mudança de path ou de
payload quebra o agendamento em silêncio (a falha fica no log do cron, no
banco, não no da API).

## 5. Variáveis de ambiente

Env var nova no código precisa existir no ambiente do Coolify **antes** do
deploy. Vazio não é sempre inofensivo: `FACEBOOK_APP_ID` vazio, por exemplo,
faz `/facebook` devolver 503.

## 6. Segredos

Nenhum segredo no diff. Vale para `.env` de exemplo, `docker-compose.yml`,
script de seed e `settings.local.json`. Removê-lo depois **não apaga do
histórico** — se vazou, o passo é **rotacionar**.

## 7. Depois do deploy

- `/` responde e reporta o `environment` esperado
- log do worker sem `unregistered task` e com a fila `marketdash-<ref>`
- `CHANGELOG.md` da raiz atualizado
- `.claude/memoria/CONTEXTO.md` reflete o novo estado; `DIARIO.md` ganhou a
  entrada do dia
