from typing import Iterable, List, Optional
from datetime import date

from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow


class DatasetRowRepository:
    def __init__(self, db: Session):
        self.db = db

    def bulk_create(self, rows: Iterable[DatasetRow]) -> None:
        self.db.add_all(list(rows))
        self.db.commit()

    def list_by_dataset(
        self,
        dataset_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[DatasetRow]:
        query = self.db.query(DatasetRow).filter(DatasetRow.dataset_id == dataset_id)
        if start_date:
            query = query.filter(DatasetRow.date >= start_date)
        if end_date:
            query = query.filter(DatasetRow.date <= end_date)
        query = query.order_by(DatasetRow.date.desc(), DatasetRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        return query.all()

    def list_by_user(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[DatasetRow]:
        query = self.db.query(DatasetRow).filter(DatasetRow.user_id == user_id)
        if start_date:
            query = query.filter(DatasetRow.date >= start_date)
        if end_date:
            query = query.filter(DatasetRow.date <= end_date)
        query = query.order_by(DatasetRow.date.desc(), DatasetRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        return query.all()
