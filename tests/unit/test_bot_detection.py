"""
Unit tests for bot detection.
Run: pytest tests/unit/test_bot_detection.py -v
"""
import pytest

from app.utils.bot_detection import is_bot


REAL_BROWSERS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
]

BOTS = [
    "facebookexternalhit/1.1",
    "WhatsApp/2.23.24.76",
    "TelegramBot (like TwitterBot)",
    "LinkedInBot/1.0",
    "Twitterbot/1.0",
    "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)",
    "Mozilla/5.0 (compatible; bingbot/2.0)",
    "DuckDuckBot/1.0",
    "curl/7.88.1",
    "python-requests/2.31.0",
    "HeadlessChrome/120.0",
    "Lighthouse",
    "Prerender (+https://prerender.io)",
    "Mozilla/5.0 (compatible; AhrefsBot/7.0)",
    "SemrushBot/7~bl",
]


@pytest.mark.parametrize("ua", REAL_BROWSERS)
def test_real_browsers_are_not_bots(ua):
    assert is_bot(ua) is False


@pytest.mark.parametrize("ua", BOTS)
def test_known_bots_are_detected(ua):
    assert is_bot(ua) is True


def test_none_is_bot():
    assert is_bot(None) is True


def test_empty_string_is_bot():
    assert is_bot("") is True


def test_short_ua_is_bot():
    assert is_bot("abc") is True


def test_whitespace_only_is_bot():
    assert is_bot("    ") is True
