"""Mapa único de features por plano.

Fonte única para backend (e espelhado no frontend). Adicionar MAX = uma entrada aqui.
"""

from __future__ import annotations

from typing import Any, Dict, FrozenSet, Optional

PlanId = str  # essencial | pro | max
PeriodId = str  # mensal | trimestral | anual

FEATURES: Dict[str, Dict[str, Any]] = {
    "essencial": {
        "menus": frozenset(
            {"dashboard", "campanhas", "upload_cliques", "indique_ganhe", "configuracoes", "planos"}
        ),
        "limites": {"paginas_captura": 0, "links": 0, "creditos_ia": 0},
        "label": "Essencial",
    },
    "pro": {
        "menus": frozenset(
            {
                "dashboard",
                "campanhas",
                "upload_cliques",
                "captura",
                "meus_links",
                "diagnostico_ia",
                "indique_ganhe",
                "configuracoes",
                "planos",
            }
        ),
        "limites": {"paginas_captura": 15, "links": 30, "creditos_ia": 200},
        "label": "Pro",
    },
    "max": {
        # Futuro — espelha Pro até haver regras próprias.
        "menus": frozenset(
            {
                "dashboard",
                "campanhas",
                "upload_cliques",
                "captura",
                "meus_links",
                "diagnostico_ia",
                "indique_ganhe",
                "configuracoes",
                "planos",
            }
        ),
        "limites": {"paginas_captura": 50, "links": 100, "creditos_ia": 1000},
        "label": "Max",
    },
}

# Menus que exigem plano Pro (cadeado no Essencial).
PRO_ONLY_MENUS: FrozenSet[str] = frozenset({"captura", "meus_links", "diagnostico_ia"})

# Checkout Kiwify por (plano, periodo) — product_id preenchido via tabela/env.
PLAN_LIST_PRICE_CENTS: Dict[tuple[str, str], int] = {
    ("essencial", "mensal"): 4700,
    ("essencial", "trimestral"): 11700,
    ("essencial", "anual"): 32700,
    ("pro", "mensal"): 6700,
    ("pro", "trimestral"): 14700,
    ("pro", "anual"): 44700,
}


def _norm_freq(frequency: Optional[str]) -> str:
    f = (frequency or "monthly").lower()
    if f in ("quarterly", "trimestral"):
        return "trimestral"
    if f in ("yearly", "annual", "anual", "year"):
        return "anual"
    return "mensal"


def list_price_cents(plan: str, frequency: str) -> Optional[int]:
    p = (plan or "").strip().lower()
    if p not in ("essencial", "pro"):
        if p == "max":
            p = "pro"
        else:
            p = "essencial" if "essenc" in p else ("pro" if "pro" in p else p)
    return PLAN_LIST_PRICE_CENTS.get((p, _norm_freq(frequency)))


CHECKOUT_LINKS: Dict[tuple[str, str], Dict[str, str]] = {
    ("essencial", "mensal"): {
        "price": "47",
        "url": "https://pay.kiwify.com.br/uMRfGkI",
    },
    ("essencial", "trimestral"): {
        "price": "117",
        "url": "https://pay.kiwify.com.br/vkKX959",
    },
    ("essencial", "anual"): {
        "price": "327",
        "url": "https://pay.kiwify.com.br/EZ81jlu",
    },
    ("pro", "mensal"): {
        "price": "67",
        "url": "https://pay.kiwify.com.br/u12boOS",
    },
    ("pro", "trimestral"): {
        "price": "147",
        "url": "https://pay.kiwify.com.br/9B9lXa6",
    },
    ("pro", "anual"): {
        "price": "447",
        "url": "https://pay.kiwify.com.br/4lhuudg",
    },
}


def normalize_plan(plan: Optional[str]) -> str:
    """Normaliza plan legado (free/marketdash) para essencial|pro|max."""
    if not plan:
        return "essencial"
    p = plan.strip().lower()
    if p in FEATURES:
        return p
    # Legado: assinantes marketdash = pro
    if p in ("marketdash", "principal", "premium"):
        return "pro"
    if p in ("free", "gratis", "gratuito"):
        return "essencial"
    return "essencial"


def plan_allows_menu(plan: Optional[str], menu_key: str) -> bool:
    cfg = FEATURES.get(normalize_plan(plan), FEATURES["essencial"])
    return menu_key in cfg["menus"]


def plan_limit(plan: Optional[str], resource: str) -> int:
    cfg = FEATURES.get(normalize_plan(plan), FEATURES["essencial"])
    return int(cfg["limites"].get(resource, 0))


def plan_has_feature(plan: Optional[str], feature: str) -> bool:
    """feature: captura | meus_links"""
    return plan_allows_menu(plan, feature)
