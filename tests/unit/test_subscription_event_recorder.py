"""Unit tests for subscription_event_recorder."""
from app.services.subscription_event_recorder import build_dedupe_key, extract_event_fields, _as_cents


def test_as_cents():
    assert _as_cents(14700) == 14700
    assert _as_cents("1130") == 1130
    assert _as_cents(None) is None


def test_dedupe_key_stable():
    k1 = build_dedupe_key("ord1", "order_approved", None)
    k2 = build_dedupe_key("ord1", "order_approved", None)
    assert k1 == k2
    assert "order_approved" in k1


def test_extract_unknown_event_type():
    payload = {
        "order": {
            "order_id": "abc",
            "webhook_event_type": "evento_futuro_xyz",
            "Customer": {"email": "a@b.com", "full_name": "A", "CPF": "123"},
            "Commissions": {"charge_amount": 4700, "kiwify_fee": 300, "my_commission": 4400},
            "Subscription": {
                "status": "active",
                "plan": {"name": "Essencial", "frequency": "monthly"},
                "customer_access": {"has_access": True, "access_until": "2030-01-01"},
            },
        }
    }
    fields = extract_event_fields(payload, "evento_futuro_xyz")
    assert fields["event_type"] == "evento_futuro_xyz"
    assert fields["amount_gross_cents"] == 4700
    assert fields["amount_net_cents"] == 4400
    assert fields["customer_email"] == "a@b.com"
    assert fields["has_access"] is True


def test_canceled_with_access_still_parsed():
    payload = {
        "order": {
            "order_id": "c1",
            "webhook_event_type": "subscription_canceled",
            "Customer": {"email": "x@y.com"},
            "Subscription": {
                "status": "canceled",
                "plan": {"name": "Pro", "frequency": "quarterly"},
                "customer_access": {"has_access": True, "access_until": "10/10/2026"},
            },
            "Commissions": {},
        }
    }
    fields = extract_event_fields(payload, "subscription_canceled")
    assert fields["has_access"] is True
    assert fields["subscription_status"] == "canceled"
    assert fields["access_until"] is not None
