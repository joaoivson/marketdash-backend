from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.page_event import PageEventCreate, PageEventResponse, SiteEventStatsResponse
from app.services.page_event_service import PageEventService

router = APIRouter(tags=["events"])


@router.get("/stats", response_model=SiteEventStatsResponse)
def get_event_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Get aggregated event counts for all user's capture sites."""
    service = PageEventService(db)
    stats = service.get_user_site_stats(current_user.id)
    return SiteEventStatsResponse(stats=stats)


@router.post("", response_model=PageEventResponse, status_code=status.HTTP_201_CREATED)
def track_event(
    event_in: PageEventCreate,
    request: Request,
    db: Session = Depends(get_db),
):
    """Track a page event (public endpoint, no auth required)."""
    ip_address = request.headers.get("x-forwarded-for", request.client.host if request.client else None)
    service = PageEventService(db)
    return service.track_event(event_in, ip_address=ip_address)
