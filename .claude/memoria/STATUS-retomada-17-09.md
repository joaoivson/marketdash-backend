# Retomada — o que ficou aberto na madrugada de 17/09/2026

**Próximo assunto combinado com o João: desenvolver e validar o módulo de
Grupos de WhatsApp.** Ele segue integralmente FORA de produção, por decisão
dele — nenhum commit de Grupos foi promovido.

## ⚠️ Dois gates de produção ABERTOS e não aprovados

| repo | run | conteúdo |
|---|---|---|
| backend | `35177611421` | filtro de período + drill-down dos cards |
| frontend | `35177613408` | idem |

`validate` e `build` verdes; as imagens **já estão no GHCR**. Só falta o clique
em "Deploy em produção". **Enquanto não aprovar, produção NÃO tem essas duas
features** — e isso é seguro, não quebrado.

Runs anteriores com só o filtro de período estão obsoletos: os novos trazem as
duas features juntas.

Falta, depois da aprovação: **validação na tela de produção** — clicar em MRR,
Faturamento e Churn e conferir se a soma da lista bate com o card. Em hml isso
não fecha: a conta `relacionamento@` existe no Supabase de produção, não no de
homologação, e toda chamada volta 401.

## 🔴 Latência do banco de produção — DECISÃO DO JOÃO PENDENTE

Medido em 17/09 03:0x UTC, isolando aplicação de banco:

| | `/` (sem banco) | `/health` (`SELECT 1`) |
|---|---|---|
| **produção** | 0,084 s | **0,577 s** |
| hml | 0,085 s | 0,100 s |

A aplicação é igualmente rápida nos dois. **Meio segundo inteiro é o banco.**
Consequência visível: `/admin/clients` (o endpoint mais pesado) estoura e o
Chrome reporta `ERR_NETWORK_CHANGED` — o João viu isso no painel.

A diferença encontrada:

```
produção:  db.iprdyorxqdiivthtcvxf.supabase.co : 5432   ← conexão DIRETA
hml:       aws-0-sa-east-1.pooler.supabase.com : 6543   ← POOLER
```

E `session.py` usa `pool_pre_ping=True`: **duas** idas ao banco por requisição.
O `README-DEPLOY.md` documenta o formato do POOLER como o esperado.

**Hipótese, não conclusão.** Falta a medição que só o VPS dá:

```bash
ssh root@31.97.22.173 'for h in db.iprdyorxqdiivthtcvxf.supabase.co aws-0-sa-east-1.pooler.supabase.com; do echo -n "$h: "; curl -s -o /dev/null -w "connect=%{time_connect}s\n" --max-time 10 "https://$h:443"; done'
```

Se o direto for muito mais lento a partir do VPS, a correção é trocar o
`DATABASE_URL` para o pooler. **Não foi feito**: é a variável mais crítica da
aplicação, exige redeploy, e não havia autorização.

⚠️ Antes de mexer: o app usa `SET LOCAL app.current_user_id` para RLS. Isso é
transação-escopo e **funciona** com pooler em modo transaction — conferido.

## Estado da infraestrutura ao fim da noite

- Pipeline novo **no ar em produção**: Actions constrói, GHCR guarda, Coolify
  puxa. Nenhum `docker build` roda no VPS.
- Teto de CPU da Hostinger **removido** às 22:03 UTC de 16/09. Benchmark do
  host: 0,99 s (era 4,70 s sob o teto).
- **hml voltou ao ar** (~02:50 UTC). CPU do host subiu de 8% para ~30-40%, RAM
  de 2,14 para 2,93 GB. Os tetos de hml estão aplicados (0,5 CPU cada, peso 256
  contra 1024 de produção).
- Migration 084 aplicada: o modal "Vincular Sub ID" saiu de `Bitmap Heap Scan`
  (custo 10.880) para `Index Only Scan` (custo 827).

## Pendências menores

| item | observação |
|---|---|
| Scan de malware | combinado com o suporte da Hostinger; leva horas, máquina ociosa é a janela |
| Avisar a Hostinger que o teto saiu | fecha o chamado — veio 3h03 depois de eu parar o probe de benchmark |
| Limpeza 16b | `aguardar-build.sh`, `trigger-deploy.sh`, `Dockerfile.worker`, Redis nº 2, envs duplicadas na API de produção |
| Alerta de queda em produção | 🚫 bloqueado: `alerta_producao_service.py` importa `waha_client`, que é código de Grupos |
| `validate` do CI não roda pytest | só `py_compile` + teste de import. Lacuna real, rodada própria |
