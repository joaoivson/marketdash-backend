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
        for item in items:
            self.db.add(item)
            self.db.flush()
        self.db.commit()
        for item in items:
            self.db.refresh(item)
        return items

    def list_by_user(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[AdSpend]:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Buscando ad_spends para user_id={user_id}, start_date={start_date}, end_date={end_date}, limit={limit}, offset={offset}")
        
        # Verificar total de registros no banco para este user_id
        total_count = self.db.query(AdSpend).filter(AdSpend.user_id == user_id).count()
        logger.info(f"Total de ad_spends encontrados para user_id={user_id}: {total_count}")
        
        # Verificar todos os user_ids únicos no banco (para debug)
        all_user_ids = self.db.query(AdSpend.user_id).distinct().all()
        logger.info(f"User IDs únicos encontrados no banco (ad_spends): {[uid[0] for uid in all_user_ids]}")
        
        query = (
            self.db.query(AdSpend)
            .options(load_only(AdSpend.id, AdSpend.date, AdSpend.amount, AdSpend.sub_id))
            .filter(AdSpend.user_id == user_id)
        )
        if start_date:
            query = query.filter(AdSpend.date >= start_date)
        if end_date:
            query = query.filter(AdSpend.date <= end_date)
        query = query.order_by(AdSpend.date.desc(), AdSpend.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        
        results = query.all()
        logger.info(f"Ad_spends retornados após filtros: {len(results)}")
        return results

    def get_by_id(self, ad_spend_id: int, user_id: int) -> Optional[AdSpend]:
        return (
            self.db.query(AdSpend)
            .filter(AdSpend.id == ad_spend_id, AdSpend.user_id == user_id)
            .first()
        )

    def delete(self, ad_spend: AdSpend) -> None:
        self.db.delete(ad_spend)
        self.db.commit()
