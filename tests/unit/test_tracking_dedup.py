"""
Unit tests for tracking deduplication helper.
Run: pytest tests/unit/test_tracking_dedup.py -v
"""
from unittest.mock import Mock, patch

from app.utils.tracking_dedup import should_count


def test_first_call_returns_true_when_redis_creates_key():
    fake_client = Mock()
    fake_client.set.return_value = True
    with patch("app.utils.tracking_dedup.get_client", return_value=fake_client):
        assert should_count("clk", 1, "1.2.3.4", "Mozilla/5.0", 60) is True
    fake_client.set.assert_called_once()
    args, kwargs = fake_client.set.call_args
    assert kwargs.get("nx") is True
    assert kwargs.get("ex") == 60


def test_duplicate_within_window_returns_false():
    fake_client = Mock()
    fake_client.set.return_value = None  # Redis returns None when NX fails
    with patch("app.utils.tracking_dedup.get_client", return_value=fake_client):
        assert should_count("clk", 1, "1.2.3.4", "Mozilla/5.0", 60) is False


def test_fail_open_when_redis_unavailable():
    with patch("app.utils.tracking_dedup.get_client", return_value=None):
        assert should_count("clk", 1, "1.2.3.4", "Mozilla/5.0", 60) is True


def test_fail_open_on_redis_exception():
    fake_client = Mock()
    fake_client.set.side_effect = RuntimeError("connection refused")
    with patch("app.utils.tracking_dedup.get_client", return_value=fake_client):
        assert should_count("clk", 1, "1.2.3.4", "Mozilla/5.0", 60) is True


def test_different_ip_ua_generates_different_keys():
    captured_keys = []
    fake_client = Mock()

    def fake_set(key, *_args, **_kwargs):
        captured_keys.append(key)
        return True

    fake_client.set.side_effect = fake_set
    with patch("app.utils.tracking_dedup.get_client", return_value=fake_client):
        should_count("clk", 1, "1.1.1.1", "Mozilla/5.0", 60)
        should_count("clk", 1, "2.2.2.2", "Mozilla/5.0", 60)
        should_count("clk", 1, "1.1.1.1", "Chrome/120", 60)
    assert len(set(captured_keys)) == 3
