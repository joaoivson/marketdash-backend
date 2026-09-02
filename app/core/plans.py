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
        "limites": {"paginas_captura": 0, "links": 0, "whatsapp_numeros": 0, "whatsapp_grupos": 0, "campanhas_grupos": 0, "whatsapp_msgs_dia": 0},
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
                "indique_ganhe",
                "configuracoes",
                "planos",
            }
        ),
        "limites": {"paginas_captura": 15, "links": 30, "whatsapp_numeros": 0, "whatsapp_grupos": 0, "campanhas_grupos": 0, "whatsapp_msgs_dia": 0},
        "label": "Pro",
    },
    "max": {
        # Ainda fora da página de vendas — liberado só por link direto Kiwify.
        "menus": frozenset(
            {
                "dashboard",
                "campanhas",
                "upload_cliques",
                "captura",
                "meus_links",
                # Automação Instagram (comentário → direct) é exclusiva do MAX.
                "automacoes",
                # Campanhas de grupos de WhatsApp (Módulo de Grupos F2+).
                "campanhas_grupos",
                # Templates de mensagem com variações (F4).
                "templates",
                "ofertas",
                "indique_ganhe",
                "configuracoes",
                "planos",
            }
        ),
        # Módulo de Grupos (decisão João 25/08): 3 números, grupos ilimitados.
        # O teto de mensagens/dia (240) é env + worker, não limite de plano aqui.
        "limites": {"paginas_captura": -1, "links": -1, "whatsapp_numeros": 3, "whatsapp_grupos": -1, "campanhas_grupos": -1,
                    # 240 (=3×80/instância) — decisão João 25/08; o teto por
                    # instância é quem manda na prática, este é o do PLANO.
                    "whatsapp_msgs_dia": 240,
                    # Monitoramento (F8): cada um faz a sessão assinar o evento
                    # `message`. O teto é de RAM e de privacidade, não comercial.
                    "monitoramentos": 3},
        "label": "Max",
    },
}

# Sentinela de "ilimitado" em plan_limit() — mantém o retorno int (sem mudar
# assinatura pra Optional). -1 nunca é um limite real válido.
UNLIMITED = -1


def is_unlimited(value: int) -> bool:
    return value == UNLIMITED

# Menus que exigem plano Pro (cadeado no Essencial).
PRO_ONLY_MENUS: FrozenSet[str] = frozenset({"captura", "meus_links"})

# Menus exclusivos do MAX (cadeado no Essencial E no Pro).
MAX_ONLY_MENUS: FrozenSet[str] = frozenset(
    {"automacoes", "campanhas_grupos", "templates", "ofertas"}
)

# Checkout Kiwify por (plano, periodo) — product_id preenchido via tabela/env.
PLAN_LIST_PRICE_CENTS: Dict[tuple[str, str], int] = {
    ("essencial", "mensal"): 4700,
    ("essencial", "trimestral"): 11700,
    ("essencial", "anual"): 32700,
    ("pro", "mensal"): 6700,
    ("pro", "trimestral"): 14700,
    ("pro", "anual"): 44700,
    # MAX faltava aqui e caía no preço do Pro (o `max → pro` de
    # list_price_cents), tirando R$30/mês do bruto por assinante Max.
    # Mesmos valores de CHECKOUT_LINKS — os dois nascem da mesma tabela.
    ("max", "mensal"): 9700,
    ("max", "trimestral"): 20700,
    ("max", "anual"): 62700,
}


def _norm_freq(frequency: Optional[str]) -> str:
    # A Kiwify não é consistente no rótulo: assinatura anual chega como
    # "yearly" em umas e "annually" em outras (caso real, Rodada 9 — a
    # "annually" caía no ramo mensal e o MRR entrava sem dividir por 12).
    f = (frequency or "monthly").lower()
    if f in ("quarterly", "trimestral", "quarter"):
        return "trimestral"
    if f in ("yearly", "annual", "annually", "anual", "year"):
        return "anual"
    return "mensal"


def list_price_cents(plan: str, frequency: str) -> Optional[int]:
    p = (plan or "").strip().lower()
    if p not in ("essencial", "pro", "max"):
        p = (
            "essencial" if "essenc" in p
            else "max" if "max" in p
            else "pro" if "pro" in p
            else p
        )
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
    ("max", "mensal"): {
        "price": "97",
        "url": "https://pay.kiwify.com.br/rTfikTj",
    },
    ("max", "trimestral"): {
        "price": "207",
        "url": "https://pay.kiwify.com.br/HPql4oU",
    },
    ("max", "anual"): {
        "price": "627",
        "url": "https://pay.kiwify.com.br/5l1Sdau",
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
