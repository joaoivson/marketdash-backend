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
    theme_color: Optional[str] = Field(None, description="Theme color hex code for background glow")
    button_color: Optional[str] = Field(None, description="Hex color for the CTA button")
    background_color: Optional[str] = Field(None, description="Hex color for the page background")
    is_gradient: Optional[bool] = Field(True, description="Whether to use a gradient background effect")
    urgency_color: Optional[str] = Field(None, description="Hex color for the urgency banner background")
    urgency_icon: Optional[str] = None
    urgency_size: Optional[str] = "md"
    urgency_icon_size: Optional[int] = 16
    urgency_animation: Optional[str] = "none"
    text_primary_color: Optional[str] = Field(None, description="Hex color for main text content")
    urgency_text_color: Optional[str] = Field(None, description="Hex color for urgency banner text")

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
