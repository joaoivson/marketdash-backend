from typing import List, Optional
from datetime import date

from fastapi import HTTPException, status

from app.models.ad_spend import AdSpend
from app.core.cache import cache_delete_prefix, cache_get, cache_set
from app.repositories.ad_spend_repository import AdSpendRepository


class AdSpendService:
    @staticmethod
    def _serialize(item: AdSpend) -> dict:
        return {
            "id": item.id,
            "date": item.date,
            "amount": item.amount,
            "sub_id": item.sub_id,
        }
    def __init__(self, repo: AdSpendRepository):
        self.repo = repo

    def create(self, user_id: int, date: date, amount: float, sub_id: Optional[str]) -> AdSpend:
        sub_id_val = None if sub_id in ["", "__all__"] else sub_id
        ad_spend = AdSpend(user_id=user_id, date=date, sub_id=sub_id_val, amount=amount)
        created = self.repo.create(ad_spend)
        cache_delete_prefix(f"ad_spends:{user_id}:")
        return created

    def bulk_create(self, user_id: int, items) -> List[AdSpend]:
        if not items:
            return []
        ad_spends = []
        for item in items:
            sub_id_val = None if item.sub_id in ["", "__all__"] else item.sub_id
            ad_spends.append(
                AdSpend(user_id=user_id, date=item.date, sub_id=sub_id_val, amount=item.amount)
            )
        created = self.repo.bulk_create(ad_spends)
        cache_delete_prefix(f"ad_spends:{user_id}:")
        return created

    def list(
        self,
        user_id: int,
        start_date: Optional[date],
        end_date: Optional[date],
        limit: Optional[int],
        offset: int,
    ) -> List[AdSpend]:
        cache_key = f"ad_spends:{user_id}:{start_date}:{end_date}:{limit}:{offset}"
        cached = cache_get(cache_key)
        if cached is not None:
            return cached
        data = self.repo.list_by_user(user_id, start_date, end_date, limit, offset)
        payload = [self._serialize(item) for item in data]
        cache_set(cache_key, payload)
        return payload

    def update(self, user_id: int, ad_spend_id: int, payload) -> AdSpend:
        ad_spend = self.repo.get_by_id(ad_spend_id, user_id)
        if not ad_spend:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro não encontrado")
        if payload.date is not None:
            ad_spend.date = payload.date
        if payload.amount is not None:
            ad_spend.amount = payload.amount
        if payload.sub_id is not None:
            ad_spend.sub_id = None if payload.sub_id in ["", "__all__"] else payload.sub_id
        self.repo.db.commit()
        self.repo.db.refresh(ad_spend)
        cache_delete_prefix(f"ad_spends:{user_id}:")
        return ad_spend

    def delete(self, user_id: int, ad_spend_id: int) -> None:
        ad_spend = self.repo.get_by_id(ad_spend_id, user_id)
        if not ad_spend:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro não encontrado")
        self.repo.delete(ad_spend)
        cache_delete_prefix(f"ad_spends:{user_id}:")
