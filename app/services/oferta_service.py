"""
Busca de ofertas no marketplace (F5) — `productOfferV2` da Shopee.

⚠️ Limitação da API que MOLDA a tela (spec §10.1, confirmada na exploração:
`productOfferV2` não existia no código):

  * `keyword` é obrigatória na prática — sem ela o retorno vem vazio mesmo
    passando `productCatId`;
  * `sortType` é ignorado quando se busca por categoria.

Consequência de produto: a tela é **busca por termo com filtros**, não um
catálogo navegável. Quando a afiliada escolhe só uma categoria, usamos o nome
da categoria COMO keyword — é o que faz a API devolver algo.

Filtros que a API não aplica (comissão mínima, preço, desconto) são aplicados
aqui, sobre a página retornada — e a tela diz que o filtro é da página.
"""
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.services.integracao_service import IntegracaoService

logger = logging.getLogger(__name__)

# `productOfferV2` — valores inlinados na string (o payload é assinado UMA vez
# por execute_graphql; pré-serializar quebraria a assinatura).
PRODUCT_OFFER_QUERY = """
{
  productOfferV2(keyword: %s, sortType: %d, page: %d, limit: %d) {
    nodes {
      itemId
      productName
      imageUrl
      price
      priceMin
      priceMax
      priceDiscountRate
      commissionRate
      commission
      sales
      shopName
      productLink
      offerLink
      ratingStar
    }
    pageInfo { page limit hasNextPage }
  }
}
"""

# sortType da Shopee: 1 relevância · 2 mais vendidos · 3 maior comissão ·
# 4 menor preço. A API ignora quando a busca é por categoria — a tela avisa.
ORDENACOES = {"relevancia": 1, "mais_vendidos": 2, "maior_comissao": 3, "menor_preco": 4}

LIMITE_MAX = 50


class BuscaInvalida(Exception):
    pass


def _decimal(valor: Any) -> float:
    try:
        return float(valor)
    except (TypeError, ValueError):
        return 0.0


def _normalizar(node: Dict[str, Any]) -> Dict[str, Any]:
    """Node cru da Shopee → o card que a tela desenha."""
    preco = _decimal(node.get("priceMin") or node.get("price"))
    desconto = _decimal(node.get("priceDiscountRate"))
    comissao_taxa = _decimal(node.get("commissionRate"))
    return {
        "item_id": str(node.get("itemId") or ""),
        "nome": node.get("productName") or "",
        "imagem_url": node.get("imageUrl") or None,
        "loja": node.get("shopName") or None,
        "preco": preco,
        "preco_de": _decimal(node.get("priceMax")) or None,
        "desconto_pct": desconto * 100 if desconto and desconto <= 1 else desconto,
        # A Shopee manda taxa em fração (0.08) — a tela mostra porcentagem.
        "comissao_pct": comissao_taxa * 100 if comissao_taxa and comissao_taxa <= 1 else comissao_taxa,
        "comissao_valor": _decimal(node.get("commission")),
        "vendas": int(_decimal(node.get("sales"))),
        "avaliacao": _decimal(node.get("ratingStar")) or None,
        # offerLink já vem com o SubID da conta; o link do GRUPO é gerado
        # depois, no envio, por generate_short_link com o sub_id certo.
        "url": node.get("productLink") or node.get("offerLink") or "",
    }


class OfertaService:
    def __init__(self, db: Session):
        self.db = db
        self.integracoes = IntegracaoService(db)

    async def buscar(self, user_id: int, keyword: Optional[str] = None,
                     categoria: Optional[str] = None,
                     ordenacao: str = "relevancia",
                     pagina: int = 1, limite: int = 20,
                     comissao_minima: Optional[float] = None,
                     preco_max: Optional[float] = None,
                     desconto_minimo: Optional[float] = None,
                     integracao_id: Optional[int] = None) -> Dict[str, Any]:
        termo = (keyword or "").strip() or (categoria or "").strip()
        # Sem termo, a `productOfferV2` devolve uma vitrine genérica da conta —
        # medido contra a API real em 26/08/2026, ao contrário do que o
        # comentário anterior aqui afirmava. É o que permite a tela abrir já com
        # ofertas em vez de um campo de busca vazio.
        # Sem termo NENHUM (ausente ou em branco) = vitrine. Um termo curto
        # demais é engano de digitação, e aí vale avisar.
        #
        # A distinção é por CONTEÚDO, não por `is None`: a tela manda `q=""` ao
        # abrir, e depender de o parâmetro sumir da query string fazia a vitrine
        # funcionar por acidente da serialização.
        vitrine = not termo
        if not vitrine and len(termo) < 2:
            raise BuscaInvalida("Digite pelo menos duas letras (ou escolha uma categoria).")

        limite = max(1, min(int(limite or 20), LIMITE_MAX))
        pagina = max(1, int(pagina or 1))
        sort = ORDENACOES.get(ordenacao, 1)

        import json as _json

        query = PRODUCT_OFFER_QUERY % (_json.dumps(termo), sort, pagina, limite)
        resultado = await self._executar(user_id, query, integracao_id)

        bloco = ((resultado or {}).get("data") or {}).get("productOfferV2") or {}
        nodes = bloco.get("nodes") or []
        page_info = bloco.get("pageInfo") or {}

        ofertas = [_normalizar(n) for n in nodes if n]
        filtradas = [
            o for o in ofertas
            if (comissao_minima is None or o["comissao_pct"] >= comissao_minima)
            and (preco_max is None or (o["preco"] and o["preco"] <= preco_max))
            and (desconto_minimo is None or o["desconto_pct"] >= desconto_minimo)
        ]
        if ordenacao == "mais_vendidos":
            # `sortType: 2` da Shopee é RANKING, não ordenação: medido contra a
            # API real, "fone" com sortType=2 devolve 17253, 11876, 5440, 12209…
            # E sem termo o sortType não muda nada — relevância e mais vendidos
            # devolvem a mesma lista. Prometer "mais vendidos" na tela só é
            # verdade se a gente ordenar de fato.
            filtradas.sort(key=lambda o: o.get("vendas") or 0, reverse=True)
        logger.info("Busca de ofertas user=%s termo=%r vitrine=%s: %s no retorno, "
                    "%s após filtros", user_id, termo or "-", vitrine,
                    len(ofertas), len(filtradas))
        return {
            "ofertas": filtradas,
            "pagina": int(page_info.get("page") or pagina),
            "tem_proxima": bool(page_info.get("hasNextPage")),
            # A tela deixa claro que os filtros valem sobre a página, não sobre
            # o catálogo — a API não filtra por comissão/preço/desconto.
            "total_na_pagina": len(ofertas),
            "termo_usado": termo,
            # A tela distingue "isto é o que você buscou" de "isto é a vitrine
            # que abrimos por padrão".
            "vitrine": vitrine,
        }

    async def _executar(self, user_id: int, query: str,
                        integracao_id: Optional[int]) -> Dict[str, Any]:
        """Assina com a credencial DA ALUNA (a comissão segue quem assina).

        Deploy A da migração: a leitura ainda usa `shopee_integrations` via
        ShopeeIntegrationService; `integracao_id` já é aceito para quando a
        usuária tiver 2+ contas (deploy B liga o caminho novo).
        """
        from app.repositories.shopee_integration_repository import (
            ShopeeIntegrationRepository,
        )
        from app.services.shopee_integration_service import ShopeeIntegrationService

        servico = ShopeeIntegrationService(ShopeeIntegrationRepository(self.db))
        return await servico.proxy_graphql(user_id, query, None)
