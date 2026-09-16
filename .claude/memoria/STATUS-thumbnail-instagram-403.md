# Thumbnail das automações do Instagram expirava (403 no console)

**Relato (16/09/2026, produção, aluno Luiz):** parede de `403 (Forbidden)` em
`scontent-gru*.cdninstagram.com` na tela de Automações.

**Causa:** o `thumbnail_url` da Graph API é uma URL **assinada e temporária**
(`oe=` = validade em epoch hexadecimal). Guardávamos o retrato no banco e nada
mais escrevia nesse campo. As URLs do relato estavam vencidas havia **3 a 9
dias**. O navegador busca a imagem **direto no CDN da Meta** — não passa pela
nossa API, não aparece em log nosso, e não tem relação com o teto de CPU da
Hostinger.

| # | Etapa | O que está sendo feito | Quem executa | Código | API | Tela |
|---|---|---|---|---|---|---|
| 1 | Diagnóstico | Decodificar o `oe=` das 8 URLs do print | eu | ✅ | — | — |
| 2 | Helper de validade | `instagram_media_url.py`: lê `oe=` sem rede | eu | ✅ | — | — |
| 3 | Renovação na listagem | `listar()` apaga a vencida e renova pela Graph API (teto 12, orçamento 8 s) | eu | ✅ | ⬜ | — |
| 4 | Rota `GET /automations` | vira `async` | eu | ✅ | ⬜ | — |
| 5 | Placeholder no front | `MiniaturaInstagram` — mesmo placeholder de "sem imagem" | eu | ✅ | — | ⬜ |
| 6 | Testes | 8 novos; suíte na baseline (2 falhas pré-existentes, 1311 passando) | eu | ✅ | — | — |
| 7 | CHANGELOG | entrada de 16/09 | eu | ✅ | — | — |
| 8 | Deploy em hml | validar com conta real conectada | eu | ⬜ | ⬜ | ⬜ |
| 9 | Deploy em produção | cherry-pick em `main` | eu | ⬜ | ⬜ | ⬜ |

**Bloqueio das etapas 8-9:** o teto de CPU da Hostinger continua ativo e o
pipeline novo (build fora do VPS) ainda não foi promovido para `main` — ver
`STATUS-build-fora-do-vps.md`. Destrava quando o benchmark de CPU do host voltar
a < 1,8 s.

## Decisões que valem para quem mexer aqui depois

- **Apagar vem antes de renovar.** URL vencida no banco é 403 garantido no
  navegador; ausência tem placeholder, quebrada não tem.
- **"Não sei dizer" nunca vira "está vencida".** URL sem `oe=` passa intacta —
  apagar URL boa deixaria a tela sem thumbnail à toa.
- **Erro da Meta na listagem não pausa nada.** `handle_token_invalido` pausa
  **todas** as automações da conta; esse efeito não pode nascer de alguém abrir
  uma tela. Quem marca o token é o envio real.
- **O seletor de publicações já estava certo** — ele busca da Graph API a cada
  abertura (cache de 15 min), então mostra URL fresca. O defeito era só do que
  ficou guardado.
