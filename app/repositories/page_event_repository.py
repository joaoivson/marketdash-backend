from sqlalchemy import func as sa_func
from sqlalchemy.orm import Session
from app.models.page_event import PageEvent


class PageEventRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, site_id: int, event_type: str, **kwargs) -> PageEvent:
        event = PageEvent(site_id=site_id, event_type=event_type, **kwargs)
        self.db.add(event)
        self.db.commit()
        self.db.refresh(event)
        return event

    def get_stats_by_site_ids(self, site_ids: list[int]) -> list[tuple]:
        if not site_ids:
            return []
        return (
            self.db.query(
                PageEvent.site_id,
                PageEvent.event_type,
                sa_func.count(PageEvent.id),
            )
            .filter(PageEvent.site_id.in_(site_ids))
            .group_by(PageEvent.site_id, PageEvent.event_type)
            .all()
        )
