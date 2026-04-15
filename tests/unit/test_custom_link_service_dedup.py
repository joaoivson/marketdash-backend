"""
Unit tests for CustomLinkService redirect: bot/prefetch/dedup guards.
Run: pytest tests/unit/test_custom_link_service_dedup.py -v
"""
from unittest.mock import Mock, patch

import pytest

from app.services.custom_link_service import CustomLinkService


@pytest.fixture
def active_link():
    link = Mock()
    link.id = 42
    link.is_active = True
    link.expires_at = None
    link.original_url = "https://shopee.com.br/produto"
    return link


@pytest.fixture
def service(active_link):
    repo = Mock()
    repo.get_by_slug.return_value = active_link
    repo.increment_click_count = Mock()
    return CustomLinkService(repo)


def _with_dedup(value: bool):
    return patch("app.services.custom_link_service.should_count", return_value=value)


def _with_bot(value: bool):
    return patch("app.services.custom_link_service.is_bot", return_value=value)


def test_prefetch_header_skips_increment(service):
    with _with_bot(False), _with_dedup(True):
        result = service.handle_redirect(
            "x", ip="1.1.1.1", user_agent="Mozilla/5.0", purpose="prefetch"
        )
    assert result == {"url": "https://shopee.com.br/produto"}
    service.repository.increment_click_count.assert_not_called()


def test_sec_purpose_prefetch_case_insensitive(service):
    with _with_bot(False), _with_dedup(True):
        service.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0", purpose="Prefetch")
    service.repository.increment_click_count.assert_not_called()


def test_bot_skips_increment(service):
    with _with_bot(True), _with_dedup(True):
        service.handle_redirect("x", ip="1.1.1.1", user_agent="facebookexternalhit/1.1")
    service.repository.increment_click_count.assert_not_called()


def test_dedup_hit_skips_increment(service):
    with _with_bot(False), _with_dedup(False):
        service.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0")
    service.repository.increment_click_count.assert_not_called()


def test_real_user_first_hit_increments(service, active_link):
    with _with_bot(False), _with_dedup(True):
        result = service.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0")
    assert result == {"url": active_link.original_url}
    service.repository.increment_click_count.assert_called_once_with(active_link)


def test_inactive_link_returns_error_without_increment():
    link = Mock()
    link.is_active = False
    repo = Mock()
    repo.get_by_slug.return_value = link
    svc = CustomLinkService(repo)
    result = svc.handle_redirect("x", ip="1.1.1.1", user_agent="Mozilla/5.0")
    assert result["status_code"] == 403
    repo.increment_click_count.assert_not_called()


def test_unknown_slug_returns_404():
    repo = Mock()
    repo.get_by_slug.return_value = None
    svc = CustomLinkService(repo)
    result = svc.handle_redirect("nope")
    assert result["status_code"] == 404
