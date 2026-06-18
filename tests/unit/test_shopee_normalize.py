"""
Unit tests for Shopee field normalization (order status + attribution type).
Run: pytest tests/unit/test_shopee_normalize.py -v
"""
import pytest

from app.utils.shopee_normalize import (
    DIRECT_ATTRIBUTION,
    CROSS_ATTRIBUTION,
    normalize_attribution_type,
    normalize_order_status,
)


# ── Status: API (inglês) → PT canônico (igual ao CSV) ─────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("PENDING", "Pendente"),
    ("COMPLETED", "Concluído"),
    ("CANCELLED", "Cancelado"),
    ("CANCELED", "Cancelado"),
    ("INVALID", "Inválido"),
    ("REJECTED", "Rejeitado"),
])
def test_status_english_to_pt(raw, expected):
    assert normalize_order_status(raw) == expected


# ── Status: CSV já em PT → idempotente (preserva hash de upsert) ──────────────
@pytest.mark.parametrize("raw", ["Pendente", "Concluído", "Cancelado"])
def test_status_pt_is_idempotent(raw):
    assert normalize_order_status(raw) == raw


def test_status_unknown_is_preserved():
    assert normalize_order_status("Reembolso Parcial") == "Reembolso Parcial"


@pytest.mark.parametrize("raw", [None, "", "  ", "nan", "None"])
def test_status_empty_like_is_none(raw):
    assert normalize_order_status(raw) is None


# ── Atribuição: CSV (PT) e API (constante) → constante canônica ───────────────
@pytest.mark.parametrize("raw", [
    "Pedido na mesma loja",
    "ORDERED_IN_SAME_SHOP",
    "ordered in same shop",
])
def test_attribution_direct(raw):
    assert normalize_attribution_type(raw) == DIRECT_ATTRIBUTION


@pytest.mark.parametrize("raw", [
    "Pedido em loja diferente",
    "ORDERED_IN_DIFFERENT_SHOP",
])
def test_attribution_cross(raw):
    assert normalize_attribution_type(raw) == CROSS_ATTRIBUTION


@pytest.mark.parametrize("raw", [None, "", "nan", "qualquer outra coisa"])
def test_attribution_unknown_is_none(raw):
    assert normalize_attribution_type(raw) is None
