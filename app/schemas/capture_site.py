from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class CaptureSiteBase(BaseModel):
    title: Optional[str] = Field(None, description="Title of the capture page")
    subtitle: Optional[str] = Field(None, description="Subtitle of the capture page")
    button_text: Optional[str] = Field(None, description="Call to action button text")
    button_link: Optional[str] = Field(None, description="URL where the button redirects")
    benefits: Optional[List[str]] = Field(None, description="List of benefits (bullet points)")
    image_url: Optional[str] = Field(None, description="URL of the logo or image")
    urgency_text: Optional[str] = Field(None, description="Urgency or scarcity text")
    slug: Optional[str] = Field(None, description="Unique slug for the public URL")

class CaptureSiteCreate(CaptureSiteBase):
    pass

class CaptureSiteUpdate(CaptureSiteBase):
    pass

class CaptureSiteResponse(CaptureSiteBase):
    id: int
    user_id: int
    slug: str
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True

class SlugCheckResponse(BaseModel):
    available: bool
    suggested_slug: str
