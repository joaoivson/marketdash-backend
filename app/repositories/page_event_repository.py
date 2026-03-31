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
