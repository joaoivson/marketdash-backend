from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base import Base


class CaptureSite(Base):
    __tablename__ = "capture_sites"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=True)
    subtitle = Column(String, nullable=True)
    button_text = Column(String, nullable=True)
    button_link = Column(String, nullable=True)
    benefits = Column(JSON, nullable=True)
    image_url = Column(String, nullable=True)
    urgency_text = Column(String, nullable=True)
    urgency_color = Column(String, nullable=True)  # HEX color for background
    urgency_text_color = Column(String, nullable=True) # HEX color for banner text
    urgency_icon = Column(String, nullable=True)   # Icon name (lucide)
    urgency_size = Column(String, nullable=True, default='md')  # 'md' or 'lg'
    urgency_icon_size = Column(Integer, default=16)
    urgency_animation = Column(String, default='none') # none, pulse, blink
    button_color = Column(String, nullable=True)   # HEX color for CTA button
    background_color = Column(String, nullable=True) # HEX color for page background
    is_gradient = Column(Boolean, default=False)   # Whether to apply gradient background
    theme_color = Column(String, nullable=True)    # HEX color for decorative elements (glow)
    text_primary_color = Column(String, nullable=True) # HEX color for main text
    slug = Column(String, unique=True, index=True, nullable=False)
    is_active = Column(Boolean, default=True)
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user = relationship("User", back_populates="capture_sites")
