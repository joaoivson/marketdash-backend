from app.core.plans import PLAN_LIST_PRICE_CENTS, list_price_cents


def test_pro_trimestral_table_price():
    assert list_price_cents("pro", "trimestral") == 14700
    assert list_price_cents("pro", "mensal") == 6700
    assert list_price_cents("essencial", "mensal") == 4700
    assert PLAN_LIST_PRICE_CENTS[("pro", "anual")] == 44700
