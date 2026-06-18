from typing import List, Optional, Tuple
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.custom_link_event import CustomLinkEvent


class CustomLinkEventRepository:
    def __init__(self, db: Session):
        self.db = db

    def add(self, custom_link_id: int, user_id: int) -> CustomLinkEvent:
        """Grava 1 evento de clique. NÃO faz commit: o commit é do chamador
        (increment_click_count), pra evento e click_count entrarem na mesma transação."""
        db_obj = CustomLinkEvent(custom_link_id=custom_link_id, user_id=user_id)
        self.db.add(db_obj)
        return db_obj

    def series(
        self, link_id: int, granularity: str, since: datetime
    ) -> List[Tuple[datetime, int]]:
        """Conta eventos agrupados por bucket de tempo (day|month), desde `since`,
        ordenado cronologicamente. Retorna [(bucket_datetime, count), ...]."""
        trunc = "month" if granularity == "month" else "day"
        bucket = func.date_trunc(trunc, CustomLinkEvent.created_at)
        rows = (
            self.db.query(bucket.label("bucket"), func.count().label("value"))
            .filter(
                CustomLinkEvent.custom_link_id == link_id,
                CustomLinkEvent.created_at >= since,
            )
            .group_by(bucket)
            .order_by(bucket)
            .all()
        )
        return [(r.bucket, r.value) for r in rows]

    def last_click(self, link_id: int) -> Optional[datetime]:
        """Timestamp do clique mais recente registrado, ou None."""
        return (
            self.db.query(func.max(CustomLinkEvent.created_at))
            .filter(CustomLinkEvent.custom_link_id == link_id)
            .scalar()
        )

    def first_click(self, link_id: int) -> Optional[datetime]:
        """Timestamp do clique mais antigo registrado (início da série), ou None."""
        return (
            self.db.query(func.min(CustomLinkEvent.created_at))
            .filter(CustomLinkEvent.custom_link_id == link_id)
            .scalar()
        )

    def count_since(self, link_id: int, since: datetime) -> int:
        """Total de eventos registrados desde `since`."""
        return (
            self.db.query(func.count())
            .filter(
                CustomLinkEvent.custom_link_id == link_id,
                CustomLinkEvent.created_at >= since,
            )
            .scalar()
            or 0
        )
