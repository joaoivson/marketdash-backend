from typing import List, Optional
from datetime import date

from sqlalchemy.orm import Session, load_only

from app.models.ad_spend import AdSpend


class AdSpendRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, ad_spend: AdSpend) -> AdSpend:
        self.db.add(ad_spend)
        self.db.commit()
        self.db.refresh(ad_spend)
        return ad_spend

    def bulk_create(self, items: List[AdSpend]) -> List[AdSpend]:
        if not items:
            return []
        self.db.add_all(items)
        self.db.commit()
        return items

    def list_by_user(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[AdSpend]:
        """Lista gastos com anúncios do usuário, sempre filtrando por user_id para garantir isolamento de dados."""
        query = (
            self.db.query(AdSpend)
            .options(load_only(AdSpend.id, AdSpend.date, AdSpend.amount, AdSpend.sub_id, AdSpend.clicks))
            .filter(AdSpend.user_id == user_id)
        )
        if start_date:
            query = query.filter(AdSpend.date >= start_date)
        if end_date:
            query = query.filter(AdSpend.date <= end_date)
        query = query.order_by(AdSpend.date.desc(), AdSpend.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        
        return query.all()

    def get_by_id(self, ad_spend_id: int, user_id: int) -> Optional[AdSpend]:
        return (
            self.db.query(AdSpend)
            .filter(AdSpend.id == ad_spend_id, AdSpend.user_id == user_id)
            .first()
        )

    def delete(self, ad_spend: AdSpend) -> None:
        self.db.delete(ad_spend)
        self.db.commit()

    def delete_all_by_user(self, user_id: int) -> int:
        """Deleta todos os ad_spends de um usuário e retorna a quantidade deletada."""
        count = self.db.query(AdSpend).filter(AdSpend.user_id == user_id).count()
        self.db.query(AdSpend).filter(AdSpend.user_id == user_id).delete()
        self.db.commit()
        return count
