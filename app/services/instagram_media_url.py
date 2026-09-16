"""Validade das URLs de mídia assinadas da Meta.

O `thumbnail_url`/`media_url` que a Graph API devolve **não é um endereço
permanente**: é uma URL assinada e temporária do CDN do Instagram, em que
`oh=` é a assinatura e `oe=` é a validade, em epoch **hexadecimal**. Passada a
validade, `scontent-*.cdninstagram.com` responde **403 para qualquer um** — não
é bloqueio da conta, do IP nem do nosso servidor.

Isso importa aqui porque `instagram_automations.media_thumbnail_url` guarda
essa URL como um retrato do instante em que a automação foi criada, e **nada
mais escreve nesse campo depois**. Como o navegador busca a imagem direto no
CDN, o 403 não passa pela nossa API e não aparece em log nenhum nosso: em
16/09/2026 a tela de Automações de uma aluna despejou uma parede de 403 no
console, com URLs vencidas havia de 3 a 9 dias.

Ler o `oe=` responde "essa URL ainda vale?" **sem nenhuma chamada de rede** —
é o que permite nunca servir URL morta e só gastar Graph API com o que
realmente venceu.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# Uma URL que vence nos próximos minutos é tratada como vencida: a tela fica
# aberta, e renovar agora custa o mesmo que renovar na próxima carga.
FOLGA_PADRAO = timedelta(minutes=10)


def validade(url: Optional[str]) -> Optional[datetime]:
    """Quando esta URL do CDN do Instagram deixa de ser aceita.

    `None` quando a URL não carrega `oe=` — inclusive quando não é uma URL da
    Meta. "Não sei dizer" nunca vira "está vencida": apagar uma URL boa deixaria
    a tela sem thumbnail sem motivo.
    """
    if not url:
        return None
    try:
        bruto = parse_qs(urlparse(url).query).get("oe", [None])[0]
        if not bruto:
            return None
        return datetime.fromtimestamp(int(bruto, 16), timezone.utc)
    except (ValueError, TypeError):
        # `oe` fora do formato esperado (a Meta já mudou o esquema antes).
        return None


def expirada(
    url: Optional[str],
    agora: Optional[datetime] = None,
    folga: timedelta = FOLGA_PADRAO,
) -> bool:
    """A URL já venceu (ou vence dentro da folga)?

    `False` para URL vazia e para URL sem `oe=` — em ambos os casos não há o que
    afirmar, e o chamador não deve descartar o que não sabe estar quebrado.
    """
    venc = validade(url)
    if venc is None:
        return False
    return venc <= (agora or datetime.now(timezone.utc)) + folga
