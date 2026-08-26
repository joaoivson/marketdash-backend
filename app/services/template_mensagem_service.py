"""
Variações de template: sorteio ponderado + placeholders (spec §4.10).

Variar o texto é anti-ban — string idêntica em 20 grupos é assinatura de bot.
O sorteio acontece POR MENSAGEM no disparo; a IA (F4) só cria variações novas
na tela de templates, nunca no caminho do envio.
"""
import random
import re
from typing import Dict, List, Optional

from app.models.roteiro import TemplateVariacao

PLACEHOLDERS = ("produto", "preco_de", "preco_por", "desconto", "loja", "link", "cupom")
_RE_PLACEHOLDER = re.compile(r"\{(" + "|".join(PLACEHOLDERS) + r")\}")


def sortear_variacao(variacoes: List[TemplateVariacao],
                     rng: Optional[random.Random] = None) -> Optional[TemplateVariacao]:
    ativas = [v for v in variacoes if v.ativa and (v.corpo or "").strip()]
    if not ativas:
        return None
    pesos = [max(int(v.peso or 1), 1) for v in ativas]
    return (rng or random).choices(ativas, weights=pesos, k=1)[0]


def preencher(corpo: str, valores: Dict[str, str]) -> str:
    """Placeholder sem valor vira vazio — nunca vaza '{preco_de}' cru na mensagem."""
    def _sub(m: re.Match) -> str:
        return str(valores.get(m.group(1)) or "")
    return _RE_PLACEHOLDER.sub(_sub, corpo)


def montar_texto(corpo: str, valores: Dict[str, str],
                 prefixo: Optional[str], sufixo: Optional[str]) -> str:
    partes = [p for p in ((prefixo or "").strip(), preencher(corpo, valores).strip(),
                          (sufixo or "").strip()) if p]
    return "\n\n".join(partes) if len(partes) > 1 else (partes[0] if partes else "")
