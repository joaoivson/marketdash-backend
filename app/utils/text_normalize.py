"""Normalização de texto compartilhada entre o matching de Sub ID e o de comentários.

São DUAS normalizações, de propósito:

- `normalizar_compacto`  — remove tudo que não é letra/número, inclusive espaço.
  Usada na comparação de Sub ID × nome de campanha, onde 'COBRE LEITO' precisa
  casar com 'cobreleito'.
- `normalizar_comentario` — COLAPSA espaço e pontuação em vez de remover.
  Usada no gatilho de comentário. Remover espaço aqui criaria falso positivo:
  o comentário "eu li nkkk" viraria "eulinkkk", que contém "link" e dispararia
  uma automação da palavra LINK.
"""

import re
import unicodedata

_NAO_ALFANUM = re.compile(r"[^a-z0-9]")
_ESPACOS = re.compile(r"\s+")


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFKD", texto or "")
    return "".join(ch for ch in decomposto if not unicodedata.combining(ch))


def normalizar_compacto(texto: str) -> str:
    """Minúsculas, sem acento, sem nada que não seja letra ou número.

    'Comente "COBRE LEITO"' -> 'comentecobreleito'
    """
    return _NAO_ALFANUM.sub("", _sem_acento(texto).lower())


def _remover_emoji(texto: str) -> str:
    """Tira emoji, seletores de variação e zero-width joiners.

    Sem isso, 'QUERO🙋‍♀️' fica com um rastro invisível colado na palavra e uma
    comparação por igualdade falharia. (Como o match é por substring, o emoji só
    atrapalha nas bordas — mas o comportamento fica imprevisível pra quem lê o
    log, então limpamos.)
    """
    limpo = []
    for ch in texto:
        categoria = unicodedata.category(ch)
        # So = símbolo "outro" (emoji), Cs = surrogate, Cf = formatação (ZWJ, VS16)
        if categoria in ("So", "Cs", "Cf"):
            limpo.append(" ")
            continue
        limpo.append(ch)
    return "".join(limpo)


def normalizar_comentario(texto: str) -> str:
    """Minúsculas, sem acento, sem emoji, pontuação virando espaço, espaços colapsados.

    'Eu quero esse!!' -> 'eu quero esse'
    'Quéro'           -> 'quero'
    'QUERO 🙋'        -> 'quero'
    """
    base = _sem_acento(texto or "").lower()
    base = _remover_emoji(base)
    # Pontuação vira espaço (não some) para não colar palavras vizinhas.
    base = re.sub(r"[^a-z0-9\s]", " ", base)
    return _ESPACOS.sub(" ", base).strip()


def comentario_casa(texto_comentario: str, palavras_normalizadas) -> bool:
    """O comentário contém alguma das palavras-chave (já normalizadas)?

    Match por SUBSTRING, decisão de produto documentada no spec §5.3:
    'QUERO' casa com 'quero', 'Quero!!', 'eu quero esse' e até 'queroo' — mas
    NÃO casa com 'queria' nem 'qro'. Substring dá exatamente esse comportamento
    sem precisar de stemming.
    """
    alvo = normalizar_comentario(texto_comentario)
    if not alvo:
        return False
    for palavra in palavras_normalizadas or []:
        if palavra and palavra in alvo:
            return True
    return False
