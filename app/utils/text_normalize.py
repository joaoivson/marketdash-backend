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
from typing import Optional

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


# --------------------------------------------------------------------------- #
#  Palavra pedida na legenda                                                   #
# --------------------------------------------------------------------------- #

# Formatos reais vistos na conta @promosdabeatrizz_ (283 publicações):
#   ✨Comente " ALGODÃO " para receber o link agora!
#   ✨Comente “PERFUME” para receber o link
#   Comente MAMADEIRA para receber
#   Comenta "BASE COREANA" que eu te mando
#
# São DOIS padrões, com tolerância diferente, e a diferença importa:
#
# - COM ASPAS a própria aspa delimita a palavra, então dá pra aceitar até 4
#   palavras ("CAIXA DE FERRAMENTAS" tem 3).
# - SEM ASPAS quem delimita é a preposição seguinte, que é um chute muito pior:
#   numa legenda corrida ela pode estar longe e arrastar meia frase junto. Daí o
#   teto de 2 palavras.
#
# ⚠️ O `\b` depois da lista de delimitadores NÃO é decorativo: sem ele, o `que`
# casa com o começo de "QUERO", e `Comente " EU QUERO " para...` extraía "EU".
_DELIMITADORES = r"(?:para|pra|pro|que|e\s+eu|no\s+coment)\b"

_COM_ASPAS = re.compile(
    r"coment[ea]\s*[\"“”\'‘’]\s*([^\"“”\'‘’\n]{2,40}?)\s*[\"“”\'‘’]",
    re.IGNORECASE,
)
# A aspa de ABERTURA é opcional aqui de propósito: legenda com aspa não fechada
# existe (`Comente “GUARDA-CHUVA para receber`) e cairia fora dos dois padrões.
# Continua protegido pelo teto de 2 palavras.
_SEM_ASPAS = re.compile(
    r"coment[ea]\s*[\"“”\'‘’]?\s*([^\"“”\'‘’\n!?.]{2,40}?)\s+" + _DELIMITADORES,
    re.IGNORECASE,
)

MAX_PALAVRAS_COM_ASPAS = 4
MAX_PALAVRAS_SEM_ASPAS = 2

# Palavras que aparecem no lugar da palavra-chave quando a legenda pede um
# genérico. Extrair uma dessas criaria uma automação de POST que dispara no
# comentário de qualquer outro post — é caso de automação "qualquer post",
# criada de propósito, não de sugestão automática.
_NAO_SAO_PALAVRA_CHAVE = frozenset({
    "aqui", "abaixo", "algo", "isso", "isto", "nos comentarios", "no comentario",
    "comentario", "comentarios", "link", "eu quero", "quero", "me manda", "manda",
})


def palavra_pedida_na_legenda(legenda: Optional[str]) -> Optional[str]:
    """A palavra que a legenda manda comentar, ou None se não der para afirmar.

    Serve para pré-preencher a automação: em 66% dos comentários medidos a
    pessoa escreve exatamente essa palavra, então ela é a palavra-chave certa.

    Devolve None de propósito quando a legenda não segue o padrão. A assimetria
    que governa o desenho: **não sugerir** custa a aluna digitar; **sugerir
    errado** cria uma automação que manda o link errado para a cliente dela. Os
    dois erros não têm o mesmo preço.
    """
    if not legenda:
        return None

    for padrao, teto in ((_COM_ASPAS, MAX_PALAVRAS_COM_ASPAS),
                         (_SEM_ASPAS, MAX_PALAVRAS_SEM_ASPAS)):
        achado = padrao.search(legenda)
        if not achado:
            continue
        partes = achado.group(1).split()
        if not partes or len(partes) > teto:
            continue
        bruta = " ".join(partes)
        if normalizar_comentario(bruta) in _NAO_SAO_PALAVRA_CHAVE:
            return None
        return bruta
    return None
