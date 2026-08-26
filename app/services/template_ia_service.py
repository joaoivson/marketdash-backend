"""
IA que GERA variações de template — nunca escreve a mensagem do envio.

A decisão (spec §4.10): chamar IA a cada disparo custaria dinheiro por
mensagem, adicionaria latência no meio do lote, inventaria preço/condição e
daria resultado imprevisível. Gerar dezenas de variações UMA vez e sortear no
envio resolve o mesmo problema (texto diferente por grupo = anti-ban) com
custo e risco zero em tempo de envio.

Contrato do prompt, nesta ordem de importância:
  1. `{link}` e demais placeholders saem intactos — texto sem o link do grupo
     quebra a atribuição de comissão, que é o produto inteiro;
  2. não inventar preço, desconto, prazo ou condição — a IA não vê a oferta;
  3. português do Brasil informal, curto, sem promessa de resultado.
"""
import json
import logging
import re
from typing import List, Optional

from app.core.config import settings
from app.services.openai_client import ErroIA, OpenAiClient
from app.services.template_mensagem_service import PLACEHOLDERS

logger = logging.getLogger(__name__)

MAX_VARIACOES = 10
MAX_CARACTERES = 400

ESTILOS = {
    "urgencia": "urgência real (estoque/tempo), sem alarme falso",
    "beneficio_direto": "benefício concreto para quem compra, direto ao ponto",
    "prova_social": "prova social leve (muita gente pegando), sem inventar número",
    "pergunta_curiosidade": "abre com pergunta curta que desperta curiosidade",
    "minimalista": "curtíssimo, duas linhas no máximo",
    "emoji_vendedora": "tom de amiga vendedora, com emojis moderados",
}

_SISTEMA = """Você escreve mensagens curtas de divulgação de ofertas para grupos
de WhatsApp, no português informal que uma afiliada brasileira usa.

REGRAS INVIOLÁVEIS:
- Mantenha TODOS os marcadores entre chaves EXATAMENTE como aparecem no texto
  base (ex.: {link}, {produto}, {preco_por}). Nunca traduza, reescreva ou
  remova um marcador. Se o texto base tem {link}, sua variação tem {link}.
- NUNCA invente preço, porcentagem de desconto, prazo, frete ou condição que
  não esteja no texto base.
- Sem promessa de resultado, sem "garantido", sem palavra de golpe.
- No máximo MAX_CHARS caracteres por variação.

Responda SOMENTE JSON: {"variacoes": ["texto 1", "texto 2", ...]}"""


class TextoBaseInvalido(Exception):
    pass


def _placeholders_de(texto: str) -> set:
    return set(re.findall(r"\{(" + "|".join(PLACEHOLDERS) + r")\}", texto or ""))


class TemplateIaService:
    def __init__(self, cliente: Optional[OpenAiClient] = None):
        self.cliente = cliente or OpenAiClient(settings.OPENAI_API_KEY,
                                               settings.OPENAI_MODELO)

    def disponivel(self) -> bool:
        return self.cliente.disponivel()

    def gerar_variacoes(self, texto_base: str, estilo: Optional[str] = None,
                        quantidade: int = 3) -> List[str]:
        """Variações válidas do texto base. Levanta ErroIA — quem chama decide
        o que mostrar; falha de IA NUNCA bloqueia o envio manual."""
        base = (texto_base or "").strip()
        if len(base) < 10:
            raise TextoBaseInvalido("Escreva um texto base de pelo menos 10 caracteres.")
        quantidade = max(1, min(int(quantidade or 3), MAX_VARIACOES))
        obrigatorios = _placeholders_de(base)

        pedido = (
            f"Texto base:\n{base}\n\n"
            f"Gere {quantidade} variações diferentes entre si"
            + (f", no estilo: {ESTILOS.get(estilo, estilo)}." if estilo else ".")
        )
        # str.format() aqui seria um tiro no pé: o prompt fala DE placeholders
        # em chaves ({link}, {produto}) e o format tentaria expandi-los.
        sistema = _SISTEMA.replace("MAX_CHARS", str(MAX_CARACTERES))
        dados, entrada, saida = self.cliente.completar_json(sistema, pedido)
        brutas = dados.get("variacoes") if isinstance(dados, dict) else None
        if not isinstance(brutas, list):
            raise ErroIA("formato", "resposta sem lista de variações")

        validas: List[str] = []
        for item in brutas:
            texto = str(item or "").strip()[:MAX_CARACTERES]
            if not texto:
                continue
            # Variação que perdeu um placeholder é descartada, não "corrigida":
            # mensagem sem {link} quebra a atribuição de comissão do grupo.
            if not obrigatorios.issubset(_placeholders_de(texto)):
                logger.info("Variação descartada: perdeu placeholder(s)")
                continue
            if texto not in validas:
                validas.append(texto)

        logger.info("IA de variações: %s pedidas, %s válidas (%s+%s tokens)",
                    quantidade, len(validas), entrada, saida)
        if not validas:
            raise ErroIA("formato", "nenhuma variação preservou os marcadores")
        return validas
