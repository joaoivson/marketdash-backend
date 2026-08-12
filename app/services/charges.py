"""Cobranças da Kiwify — uma cobrança, uma chave: `order_ref`.

Rodada 6, item 1. Antes, cobrança era reconstruída do array cumulativo
`Subscription.charges.completed[]`, deduplicado pelo `order_id` interno da
Kiwify. O import histórico (scripts/import_kiwify_historico.py) injetou um
array sintético cujo `order_id` era na verdade o `order_ref` do export — duas
chaves para a mesma cobrança, faturamento e total pago dobrados.

Agora a cobrança É o evento pago, chaveado pelo `order_ref` que existe tanto no
topo do payload do webhook quanto no "ID da venda" do export usado no import.
O array não gera mais cobrança: vira só verificação (ver `unknown_array_charges`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from app.core.plans import list_price_cents

PAID_EVENT_TYPES = {
    "order_approved",
    "subscription_renewed",
    "compra_aprovada",
}


def is_import_event(ev) -> bool:
    """Evento veio do import histórico (não de um webhook próprio da cobrança)."""
    return (getattr(ev, "dedupe_key", None) or "").startswith("import:")


def charge_key(ev) -> Optional[str]:
    """Identidade da cobrança. `order_ref` é a chave comum entre import e webhook.

    Cai no `order_id` para eventos legados gravados antes de `order_ref` existir;
    em último caso usa o id do próprio evento, que nunca colide (e portanto nunca
    deduplica — comportamento correto quando não há como saber que é a mesma
    cobrança).
    """
    ref = (getattr(ev, "order_ref", None) or "").strip()
    if ref:
        return f"ref:{ref}"
    oid = (getattr(ev, "order_id", None) or "").strip()
    if oid:
        return f"oid:{oid}"
    ident = getattr(ev, "id", None)
    return f"ev:{ident}" if ident is not None else None


def _normalize_plan_label(name: Optional[str], plan_id: Optional[str] = None) -> str:
    blob = f"{name or ''} {plan_id or ''}".lower()
    if "max" in blob:
        return "max"
    if "pro" in blob:
        return "pro"
    return "essencial"


def _better(candidato, atual) -> bool:
    """Qual dos dois eventos representa melhor a mesma cobrança.

    Ordem: webhook ganha do import (o `my_commission` do webhook é a fonte
    autoritativa do líquido); depois quem tem líquido preenchido; depois o mais
    antigo por `received_at`, só para o resultado ser estável entre queries.
    """
    if is_import_event(candidato) != is_import_event(atual):
        return is_import_event(atual)  # atual é import, candidato não → troca

    tem_net_cand = getattr(candidato, "amount_net_cents", None) is not None
    tem_net_atual = getattr(atual, "amount_net_cents", None) is not None
    if tem_net_cand != tem_net_atual:
        return tem_net_cand

    r_cand = getattr(candidato, "received_at", None)
    r_atual = getattr(atual, "received_at", None)
    if r_cand is not None and r_atual is not None:
        return r_cand < r_atual
    return False


def _as_charge(ev) -> Dict[str, Any]:
    net = getattr(ev, "amount_net_cents", None) or 0
    gross = getattr(ev, "amount_gross_cents", None) or 0
    fee = getattr(ev, "fee_cents", None)

    plan = _normalize_plan_label(
        getattr(ev, "plan_name", None), getattr(ev, "plan_id", None)
    )
    frequency = getattr(ev, "plan_frequency", None) or "monthly"

    if not gross:
        tabela = list_price_cents(plan, frequency)
        gross = tabela if tabela is not None else net

    return {
        "order_ref": (getattr(ev, "order_ref", None) or getattr(ev, "order_id", None) or ""),
        "net_cents": net,
        "gross_cents": gross,
        "fee_cents": fee if fee is not None else max(gross - net, 0),
        "paid_at": getattr(ev, "approved_date", None) or getattr(ev, "received_at", None),
        "plan": plan,
        "frequency": frequency,
        "from_import": is_import_event(ev),
    }


def extract_paid_charges(events) -> List[Dict[str, Any]]:
    """Cobranças pagas distintas da lista de eventos, uma por `order_ref`."""
    melhor: Dict[str, Any] = {}
    for ev in events:
        if (getattr(ev, "event_type", None) or "").lower() not in PAID_EVENT_TYPES:
            continue
        chave = charge_key(ev)
        if not chave:
            continue
        atual = melhor.get(chave)
        if atual is None or _better(ev, atual):
            melhor[chave] = ev
    return [_as_charge(ev) for ev in melhor.values()]


def total_paid_net(events) -> int:
    return sum(c["net_cents"] for c in extract_paid_charges(events))


def _charges_completed_for_event(ev) -> list:
    """O array cumulativo do webhook, do campo dedicado ou do payload cru."""
    completed = getattr(ev, "charges_completed", None)
    if isinstance(completed, list) and completed:
        return completed
    raw = getattr(ev, "raw_payload", None)
    if not isinstance(raw, dict):
        return []
    for key in ("Subscription", "subscription"):
        sub = raw.get(key)
        if not isinstance(sub, dict):
            continue
        charges = sub.get("charges")
        if isinstance(charges, dict) and isinstance(charges.get("completed"), list):
            return charges["completed"]
    return []


def unknown_array_charges(events, known_ids: Set[str]) -> List[Dict[str, Any]]:
    """Entradas `paid` do array que não correspondem a nenhuma cobrança conhecida.

    Verificação, não fonte: a resposta correta a um achado aqui é investigar um
    webhook perdido, nunca inserir a cobrança automaticamente.
    """
    desconhecidas: List[Dict[str, Any]] = []
    vistos: Set[str] = set()
    for ev in events:
        for ch in _charges_completed_for_event(ev):
            if not isinstance(ch, dict):
                continue
            if (ch.get("status") or "").lower() != "paid":
                continue
            oid = ch.get("order_id")
            if not oid:
                continue
            oid = str(oid)
            if oid in known_ids or oid in vistos:
                continue
            vistos.add(oid)
            desconhecidas.append(ch)
    return desconhecidas
