#!/usr/bin/env python3
"""Injeta um webhook de comentário assinado, como se viesse da Meta.

POR QUE ISSO EXISTE
-------------------
A doc da Meta diz que o campo de webhook `comments` só entrega notificação com
**Advanced Access** e com o app em **Live**. Ou seja: enquanto o App Review não
sair, comentar de verdade no post NÃO faz a Meta chamar o nosso webhook — e o
teste ponta a ponta parece "quebrado" sem estar.

Este script fecha essa lacuna: monta o payload no formato real, assina com o
`INSTAGRAM_APP_SECRET` (mesma assinatura que a Meta usa) e faz o POST. Dali em
diante TUDO é real — matching, dedupe, janela, throttle e o envio do direct pela
API. O único passo simulado é a entrega da notificação.

USO
---
    export INSTAGRAM_APP_SECRET=<o mesmo do backend>
    python scripts/simular_comentario_instagram.py \
        --url https://api.hml.marketdash.com.br/webhooks/instagram \
        --ig-user-id 17841400000000000 \
        --media-id 18000000000000000 \
        --texto "quero" \
        --commenter-id 900001 \
        --commenter-username maria.silva

Cada execução usa um `comment_id` novo (timestamp) — para testar DEDUPE, repita
o mesmo id com `--comment-id`.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.request


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--url", required=True, help="URL completa do webhook")
    p.add_argument("--ig-user-id", required=True, help="ig_user_id da conta conectada")
    p.add_argument("--media-id", required=True, help="id do post")
    p.add_argument("--texto", default="quero", help="texto do comentário")
    p.add_argument("--commenter-id", default="900001")
    p.add_argument("--commenter-username", default="maria.silva")
    p.add_argument("--comment-id", default=None, help="repita o mesmo para testar dedupe")
    p.add_argument(
        "--idade-dias",
        type=float,
        default=0.0,
        help="envelhece o comentário — use 8 para testar a janela de 7 dias",
    )
    p.add_argument(
        "--story",
        action="store_true",
        help=(
            "simula um REPLY DE STORY (webhook `messages`) em vez de comentário. "
            "--media-id vira o id do story; --comment-id vira o mid; a janela é "
            "de 24h (use --idade-dias 1.1 para testar)."
        ),
    )
    args = p.parse_args()

    segredo = os.environ.get("INSTAGRAM_APP_SECRET")
    if not segredo:
        print("ERRO: defina INSTAGRAM_APP_SECRET (o mesmo do backend).", file=sys.stderr)
        return 2

    agora = int(time.time())
    ts = agora - int(args.idade_dias * 86_400)
    comment_id = args.comment_id or f"sim-{agora}"

    if args.story:
        # Reply de story: formato do webhook `messages` (timestamp em MILISSEGUNDOS,
        # mid no lugar de comment_id, story em reply_to). O pipeline responde com a
        # Send API por recipient id — dali em diante tudo é real.
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": args.ig_user_id,
                    "time": ts,
                    "messaging": [
                        {
                            "sender": {"id": args.commenter_id},
                            "recipient": {"id": args.ig_user_id},
                            "timestamp": ts * 1000,
                            "message": {
                                "mid": comment_id,
                                "text": args.texto,
                                "reply_to": {
                                    "story": {
                                        "id": args.media_id,
                                        "url": "https://cdn.example.invalid/story.mp4",
                                    }
                                },
                            },
                        }
                    ],
                }
            ],
        }
    else:
        payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": args.ig_user_id,
                    "time": ts,
                    "changes": [
                        {
                            "field": "comments",
                            "value": {
                                "id": comment_id,
                                "text": args.texto,
                                "timestamp": ts,
                                "from": {
                                    "id": args.commenter_id,
                                    "username": args.commenter_username,
                                },
                                "media": {"id": args.media_id, "media_product_type": "FEED"},
                            },
                        }
                    ],
                }
            ],
        }

    # A assinatura é sobre os BYTES enviados — reserializar muda espaços e quebra o HMAC.
    corpo = json.dumps(payload).encode("utf-8")
    assinatura = "sha256=" + hmac.new(segredo.encode("utf-8"), corpo, hashlib.sha256).hexdigest()

    req = urllib.request.Request(
        args.url,
        data=corpo,
        headers={"Content-Type": "application/json", "X-Hub-Signature-256": assinatura},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resposta:
            print(f"HTTP {resposta.status} · comment_id={comment_id}")
            print(resposta.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        print(f"HTTP {exc.code}: {exc.read().decode('utf-8')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
