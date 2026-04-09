from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


class PageEventCreate(BaseModel):
    site_id: int = Field(..., description="Capture site ID")
    slug: str = Field(..., description="Capture site slug for validation")
    event_type: str = Field(..., description="Event type: page_view or click_group")
    utm_source: Optional[str] = None
    utm_medium: Optional[str] = None
    utm_campaign: Optional[str] = None
    utm_adset: Optional[str] = None
    utm_ad: Optional[str] = None
    referrer: Optional[str] = None
    user_agent: Optional[str] = None


class PageEventResponse(BaseModel):
    id: int
    site_id: int
    event_type: str
    utm_source: Optional[str] = None
    utm_campaign: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class SiteEventStats(BaseModel):
    site_id: int
    page_views: int = 0
    click_groups: int = 0


class SiteEventStatsResponse(BaseModel):
    stats: list[SiteEventStats]
