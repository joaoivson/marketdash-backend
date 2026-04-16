from app.repositories.user_settings_repository import UserSettingsRepository


class UserSettingsService:
    def __init__(self, repo: UserSettingsRepository):
        self.repo = repo

    def get_settings(self, user_id: int) -> dict:
        settings = self.repo.get_by_user(user_id)
        if settings is None:
            return {"ad_tax_rate": 0.0, "commission_tax_rate": 0.0}
        return {"ad_tax_rate": settings.ad_tax_rate, "commission_tax_rate": settings.commission_tax_rate}

    def update_settings(self, user_id: int, ad_tax_rate: float, commission_tax_rate: float) -> dict:
        settings = self.repo.upsert(user_id, ad_tax_rate, commission_tax_rate)
        return {"ad_tax_rate": settings.ad_tax_rate, "commission_tax_rate": settings.commission_tax_rate}
