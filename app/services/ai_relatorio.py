"""
Forma mínima do relatório devolvido pela IA.

`completar_json` garante que a resposta é JSON válido — não que é um relatório.
Um `{"erro": "..."}` ou um objeto sem seções passa por ele, e o débito de 10
créditos acontece logo em seguida: a afiliada pagaria por uma tela em branco.
A tela tem blindagem contra campo faltando, o que a torna silenciosa nesse
caso — motivo a mais para a checagem viver aqui, antes de cobrar.

O critério é o do produto, não o do schema: existe um resumo E existe pelo
menos uma seção com conteúdo. Campo a mais não incomoda; campo a menos vira
lista vazia, para o resto do código não precisar de `or []` a cada uso.
"""
from typing import Any, Dict

LISTAS = ("escalar", "pausar", "observar", "detalhamento",
          "proximos_passos", "perguntas_sugeridas")

# Seções que sustentam um relatório: se nenhuma delas veio, não há o que ler.
SECOES_DE_CONTEUDO = ("escalar", "pausar", "observar", "detalhamento", "proximos_passos")


class RelatorioInvalido(Exception):
    pass


def validar_relatorio(bruto: Any) -> Dict[str, Any]:
    if not isinstance(bruto, dict):
        raise RelatorioInvalido(f"esperava objeto, veio {type(bruto).__name__}")

    # Sem mínimo de tamanho: um resumo curto ainda é um resumo, e barrar por
    # contagem de caracteres recusaria relatório bom por régua inventada. O que
    # importa é existir texto e existir seção — as duas coisas juntas.
    resumo = bruto.get("resumo_executivo")
    if not isinstance(resumo, str) or not resumo.strip():
        raise RelatorioInvalido("resumo_executivo ausente ou vazio")

    relatorio = dict(bruto)
    relatorio["resumo_executivo"] = resumo.strip()

    # Lista que veio como outra coisa (string, dict, null) é tratada como
    # ausente: renderizar meia estrutura é pior do que não renderizar.
    for chave in LISTAS:
        valor = relatorio.get(chave)
        relatorio[chave] = valor if isinstance(valor, list) else []

    if not any(relatorio[chave] for chave in SECOES_DE_CONTEUDO):
        raise RelatorioInvalido("nenhuma seção com conteúdo")

    numeros = relatorio.get("numeros")
    relatorio["numeros"] = numeros if isinstance(numeros, dict) else {}

    return relatorio
