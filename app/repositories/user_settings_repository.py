from typing import Optional
from sqlalchemy.orm import Session
from app.models.user_settings import UserSettings


class UserSettingsRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_user(self, user_id: int) -> Optional[UserSettings]:
        return self.db.query(UserSettings).filter(UserSettings.user_id == user_id).first()

    def upsert(self, user_id: int, ad_tax_rate: float, commission_tax_rate: float) -> UserSettings:
        existing = self.get_by_user(user_id)
        if existing:
            existing.ad_tax_rate = ad_tax_rate
            existing.commission_tax_rate = commission_tax_rate
            self.db.commit()
            self.db.refresh(existing)
            return existing
        new_settings = UserSettings(
            user_id=user_id,
            ad_tax_rate=ad_tax_rate,
            commission_tax_rate=commission_tax_rate,
        )
        self.db.add(new_settings)
        self.db.commit()
        self.db.refresh(new_settings)
        return new_settings
