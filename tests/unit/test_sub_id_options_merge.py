"""Testes do merge de Sub IDs (venda + cliques) no modal de vínculo."""
from app.services.campaign_service import merge_sub_id_option_rows


def test_merge_includes_clicks_only_sub_id():
    sales = [{"sub_id": "legging100", "orders": 3, "commission": 45.5}]
    clicks = ["MacacaoListrado1", "legging100----"]  # segundo deve dedupar com sale
    opts = merge_sub_id_option_rows(
        sales=sales,
        click_sub_ids=clicks,
        linked={},
        campaign_id=1,
        campaign_name="Campanha Macacao Listrado",
    )
    ids = [o.sub_id for o in opts]
    assert "legging100" in ids
    assert any(_normish(i) == "macacaolistrado1" for i in ids)
    assert len([i for i in ids if _normish(i) == "legging100"]) == 1


def test_dedupe_case_insensitive_prefers_sales():
    sales = [{"sub_id": "macacaolistrado1", "orders": 2, "commission": 10.0}]
    clicks = ["MacacaoListrado1"]
    opts = merge_sub_id_option_rows(sales, clicks, {}, 1, "x")
    assert len(opts) == 1
    assert opts[0].sub_id == "macacaolistrado1"
    assert opts[0].orders == 2
    assert opts[0].commission == 10.0


def test_sort_sales_before_zero_orders():
    sales = [
        {"sub_id": "a", "orders": 1, "commission": 1.0},
        {"sub_id": "b", "orders": 5, "commission": 9.0},
    ]
    clicks = ["z_sem_venda"]
    opts = merge_sub_id_option_rows(sales, clicks, {}, 1, "x")
    assert [o.sub_id for o in opts] == ["b", "a", "z_sem_venda"]


def test_linked_other_campaign_blocks_by_normalized_key():
    sales = []
    clicks = ["MacacaoListrado1"]
    linked = {"macacaolistrado1": (99, "Outra")}
    opts = merge_sub_id_option_rows(sales, clicks, linked, campaign_id=1, campaign_name="Macacao")
    assert len(opts) == 1
    assert opts[0].linked_campaign_id == 99
    assert opts[0].linked_campaign_name == "Outra"
    # Cadeado: seleção bloqueada no modal (linked_campaign_id preenchido).


def test_own_link_does_not_lock():
    clicks = ["foo"]
    linked = {"foo": (7, "Eu mesma")}
    opts = merge_sub_id_option_rows([], clicks, linked, campaign_id=7, campaign_name="x")
    assert opts[0].linked_campaign_id is None


def _normish(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())
