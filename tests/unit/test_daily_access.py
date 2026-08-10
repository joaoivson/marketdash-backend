"""Unit tests for daily access idempotency (1 record/user/BRT-day)."""
from unittest.mock import MagicMock, Mock

from app.models.user_login import UserLogin
from app.services.daily_access_service import record_daily_access


def _mock_db(*, exists: bool):
    db = MagicMock()
    query = db.query.return_value
    filt = query.filter.return_value
    filt.first.return_value = (1,) if exists else None
    return db


def test_record_daily_access_inserts_when_none_today():
    db = _mock_db(exists=False)

    record_daily_access(db, user_id=42)

    db.add.assert_called_once()
    added = db.add.call_args[0][0]
    assert isinstance(added, UserLogin)
    assert added.user_id == 42
    assert added.logged_at is not None
    db.commit.assert_called_once()


def test_record_daily_access_skips_when_exists_today():
    db = _mock_db(exists=True)

    record_daily_access(db, user_id=42)

    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_record_daily_access_grava_ip_e_user_agent_quando_disponiveis():
    """Item 11 (Rodada 5): sem isso, é impossível investigar um outlier de
    acessos (várias sessões reais vs. bug de sessão reautenticando)."""
    db = _mock_db(exists=False)

    record_daily_access(db, user_id=42, ip="203.0.113.5", user_agent="Mozilla/5.0")

    added = db.add.call_args[0][0]
    assert added.ip == "203.0.113.5"
    assert added.user_agent == "Mozilla/5.0"
