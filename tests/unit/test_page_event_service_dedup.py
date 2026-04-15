"""
Unit tests for PageEventService.track_event: preview/bot/dedup guards.
Run: pytest tests/unit/test_page_event_service_dedup.py -v
"""
from unittest.mock import MagicMock, Mock, patch

import pytest

from app.schemas.page_event import PageEventCreate
from app.services.page_event_service import PageEventService


@pytest.fixture
def active_site():
    site = Mock()
    site.id = 7
    site.slug = "promo"
    site.is_active = True
    return site


@pytest.fixture
def mock_db(active_site):
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value.first.return_value = active_site
    return db


def _payload(**overrides) -> PageEventCreate:
    base = dict(
        site_id=7,
        slug="promo",
        event_type="page_view",
        user_agent="Mozilla/5.0 (Macintosh)",
    )
    base.update(overrides)
    return PageEventCreate(**base)


def test_preview_flag_skips_insert(mock_db):
    svc = PageEventService(mock_db)
    svc.repo = Mock()
    result = svc.track_event(_payload(preview=True), ip_address="1.1.1.1")
    assert result is None
    svc.repo.create.assert_not_called()


def test_bot_user_agent_skips_insert(mock_db):
    svc = PageEventService(mock_db)
    svc.repo = Mock()
    with patch("app.services.page_event_service.is_bot", return_value=True):
        result = svc.track_event(_payload(), ip_address="1.1.1.1")
    assert result is None
    svc.repo.create.assert_not_called()


def test_dedup_hit_skips_insert(mock_db):
    svc = PageEventService(mock_db)
    svc.repo = Mock()
    with patch("app.services.page_event_service.is_bot", return_value=False), \
         patch("app.services.page_event_service.should_count", return_value=False):
        result = svc.track_event(_payload(), ip_address="1.1.1.1")
    assert result is None
    svc.repo.create.assert_not_called()


def test_valid_first_hit_inserts(mock_db):
    svc = PageEventService(mock_db)
    svc.repo = Mock()
    svc.repo.create.return_value = Mock(id=1)
    with patch("app.services.page_event_service.is_bot", return_value=False), \
         patch("app.services.page_event_service.should_count", return_value=True):
        result = svc.track_event(_payload(), ip_address="1.1.1.1")
    assert result is not None
    svc.repo.create.assert_called_once()


def test_invalid_event_type_raises(mock_db):
    from fastapi import HTTPException

    svc = PageEventService(mock_db)
    with pytest.raises(HTTPException) as exc:
        svc.track_event(_payload(event_type="invalid"), ip_address="1.1.1.1")
    assert exc.value.status_code == 400
