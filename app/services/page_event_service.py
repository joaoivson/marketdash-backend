import logging
from fastapi import HTTPException, status
from app.models.capture_site import CaptureSite
from app.repositories.page_event_repository import PageEventRepository
from app.schemas.page_event import PageEventCreate
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

VALID_EVENT_TYPES = {"page_view", "click_group"}


class PageEventService:
    def __init__(self, db: Session):
        self.db = db
        self.repo = PageEventRepository(db)

    def track_event(self, event_data: PageEventCreate, ip_address: str | None = None):
        if event_data.event_type not in VALID_EVENT_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid event_type. Must be one of: {', '.join(VALID_EVENT_TYPES)}"
            )

        site = self.db.query(CaptureSite).filter(
            CaptureSite.id == event_data.site_id,
            CaptureSite.slug == event_data.slug,
            CaptureSite.is_active == True
        ).first()

        if not site:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Capture site not found"
            )

        event = self.repo.create(
            site_id=event_data.site_id,
            event_type=event_data.event_type,
            utm_source=event_data.utm_source,
            utm_medium=event_data.utm_medium,
            utm_campaign=event_data.utm_campaign,
            utm_adset=event_data.utm_adset,
            utm_ad=event_data.utm_ad,
            referrer=event_data.referrer,
            user_agent=event_data.user_agent,
            ip_address=ip_address,
        )

        logger.info(f"Event tracked: {event_data.event_type} for site {event_data.slug}")
        return event
