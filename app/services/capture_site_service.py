from typing import List, Optional
import uuid
from app.models.capture_site import CaptureSite
from app.schemas.capture_site import CaptureSiteCreate, CaptureSiteUpdate, SlugCheckResponse
from app.repositories.capture_site_repository import CaptureSiteRepository

class CaptureSiteService:
    def __init__(self, repository: CaptureSiteRepository):
        self.repository = repository

    def get_user_sites(self, user_id: int) -> List[CaptureSite]:
        return self.repository.get_by_user(user_id)

    def get_site(self, site_id: int) -> Optional[CaptureSite]:
        return self.repository.get(site_id)

    def get_site_by_slug(self, slug: str) -> Optional[CaptureSite]:
        return self.repository.get_by_slug(slug)

    def check_slug(self, slug: str) -> SlugCheckResponse:
        existing = self.repository.get_by_slug(slug)
        if not existing:
            return SlugCheckResponse(available=True, suggested_slug=slug)
        
        # If exists, suggest a new one
        random_suffix = uuid.uuid4().hex[:4]
        suggested = f"{slug}-{random_suffix}"
        return SlugCheckResponse(available=False, suggested_slug=suggested)

    def create_site(self, user_id: int, site_in: CaptureSiteCreate) -> CaptureSite:
        # Check slug uniqueness before creation
        if site_in.slug:
            existing = self.repository.get_by_slug(site_in.slug)
            if existing:
                random_suffix = uuid.uuid4().hex[:4]
                site_in.slug = f"{site_in.slug}-{random_suffix}"
        else:
            # Generate a random slug if none provided
            site_in.slug = uuid.uuid4().hex[:8]
            
        return self.repository.create(user_id, site_in)

    def update_site(self, site_id: int, user_id: int, site_in: CaptureSiteUpdate) -> Optional[CaptureSite]:
        site = self.repository.get(site_id)
        if not site or site.user_id != user_id:
            return None
            
        # Check slug uniqueness if it's being updated
        if site_in.slug and site_in.slug != site.slug:
            existing = self.repository.get_by_slug(site_in.slug)
            if existing:
                random_suffix = uuid.uuid4().hex[:4]
                site_in.slug = f"{site_in.slug}-{random_suffix}"
                
        return self.repository.update(site, site_in)

    def delete_site(self, site_id: int, user_id: int) -> bool:
        site = self.repository.get(site_id)
        if not site or site.user_id != user_id:
            return False
            
        self.repository.delete(site_id)
        return True
