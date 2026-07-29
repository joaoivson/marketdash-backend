"""Registro idempotente de último acesso (1 registro/usuário/dia BRT)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.models.user_login import UserLogin


def record_daily_access(db: Session, user_id: int) -> None:
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    start = datetime(today.year, today.month, today.day, tzinfo=ZoneInfo("America/Sao_Paulo")).astimezone(
        timezone.utc
    )
    end = start + timedelta(days=1)
    exists = (
        db.query(UserLogin.id)
        .filter(
            UserLogin.user_id == user_id,
            UserLogin.logged_at >= start,
            UserLogin.logged_at < end,
        )
        .first()
    )
    if exists:
        return
    db.add(UserLogin(user_id=user_id, logged_at=datetime.now(timezone.utc)))
    db.commit()
