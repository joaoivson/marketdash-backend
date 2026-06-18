"""Normalização de campos Shopee para um vocabulário canônico, independente da
fonte (CSV de comissões ou API GraphQL).

Motivação:
- O CSV traz "Status do Pedido" em PT ("Concluído", "Pendente", "Cancelado") e
  "Tipo de Atribuição" em PT ("Pedido na mesma loja", "Pedido em loja diferente").
- A API GraphQL traz `orderStatus` em inglês/maiúsculo ("COMPLETED", "PENDING", ...)
  e `attributionType` em constantes ("ORDERED_IN_SAME_SHOP", "ORDERED_IN_DIFFERENT_SHOP").

Convergimos ambas as fontes para o mesmo formato para que o dashboard exiba sempre
o status em português (igual ao CSV) e o KPI "Diretos" funcione em qualquer origem.
Ambas as funções são idempotentes (rótulo canônico → ele mesmo).
"""
import unicodedata
from typing import Optional

_EMPTY_KEYS = {"", "nan", "none", "null"}


def _norm_key(value: str) -> str:
    """minúsculo, sem acentos, espaços colapsados — para casar variações de grafia."""
    nfkd = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(ascii_only.lower().split())


# ── Status do pedido ──────────────────────────────────────────────────────────
# inglês (API) e variações PT → rótulo canônico em PT (idêntico ao CSV Shopee).
_ORDER_STATUS_MAP = {
    "pending": "Pendente",
    "pendente": "Pendente",
    "completed": "Concluído",
    "complete": "Concluído",
    "concluido": "Concluído",
    "cancelled": "Cancelado",
    "canceled": "Cancelado",
    "cancelado": "Cancelado",
    "invalid": "Inválido",
    "invalido": "Inválido",
    "pedido invalido": "Inválido",
    "rejected": "Rejeitado",
    "rejeitado": "Rejeitado",
}


def normalize_order_status(raw: Optional[str]) -> Optional[str]:
    """Converte o status do pedido para PT canônico. Desconhecido → mantém o valor
    original (apenas com strip), para não perder informação de status raros."""
    if raw is None:
        return None
    key = _norm_key(str(raw))
    if key in _EMPTY_KEYS:
        return None
    return _ORDER_STATUS_MAP.get(key, str(raw).strip())


# ── Tipo de atribuição ────────────────────────────────────────────────────────
# Constantes da Shopee usadas pelo dashboard/KPI "Diretos" (back e front).
DIRECT_ATTRIBUTION = "ORDERED_IN_SAME_SHOP"          # comprou na mesma loja do clique
CROSS_ATTRIBUTION = "ORDERED_IN_DIFFERENT_SHOP"      # cookie / cross-shop (até 7 dias)

# CSV (PT) e API (constantes) → constante canônica da API.
_ATTRIBUTION_MAP = {
    "pedido na mesma loja": DIRECT_ATTRIBUTION,
    "ordered in same shop": DIRECT_ATTRIBUTION,
    "ordered_in_same_shop": DIRECT_ATTRIBUTION,
    "same shop": DIRECT_ATTRIBUTION,
    "pedido em loja diferente": CROSS_ATTRIBUTION,
    "ordered in different shop": CROSS_ATTRIBUTION,
    "ordered_in_different_shop": CROSS_ATTRIBUTION,
    "different shop": CROSS_ATTRIBUTION,
}


def normalize_attribution_type(raw: Optional[str]) -> Optional[str]:
    """Converte o tipo de atribuição para a constante canônica da Shopee.
    Desconhecido/vazio → None (não conta como pedido direto)."""
    if raw is None:
        return None
    key = _norm_key(str(raw))
    if key in _EMPTY_KEYS:
        return None
    return _ATTRIBUTION_MAP.get(key)
