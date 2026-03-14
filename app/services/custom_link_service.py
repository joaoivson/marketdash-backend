from typing import List, Optional
from datetime import datetime, timezone
import uuid
from app.models.custom_link import CustomLink
from app.schemas.custom_link import CustomLinkCreate, CustomLinkUpdate, SlugCheckResponse
from app.repositories.custom_link_repository import CustomLinkRepository


class CustomLinkService:
    def __init__(self, repository: CustomLinkRepository):
        self.repository = repository

    def get_user_links(self, user_id: int) -> List[CustomLink]:
        return self.repository.get_by_user(user_id)

    def get_link(self, link_id: int) -> Optional[CustomLink]:
        return self.repository.get(link_id)

    def get_link_by_slug(self, slug: str) -> Optional[CustomLink]:
        return self.repository.get_by_slug(slug)

    def check_slug(self, slug: str) -> SlugCheckResponse:
        existing = self.repository.get_by_slug(slug)
        if not existing:
            return SlugCheckResponse(available=True, suggested_slug=slug)

        random_suffix = uuid.uuid4().hex[:4]
        suggested = f"{slug}-{random_suffix}"
        return SlugCheckResponse(available=False, suggested_slug=suggested)

    def create_link(self, user_id: int, link_in: CustomLinkCreate) -> CustomLink:
        if link_in.slug:
            existing = self.repository.get_by_slug(link_in.slug)
            if existing:
                random_suffix = uuid.uuid4().hex[:4]
                link_in.slug = f"{link_in.slug}-{random_suffix}"
        else:
            link_in.slug = uuid.uuid4().hex[:8]

        return self.repository.create(user_id, link_in)

    def update_link(self, link_id: int, user_id: int, link_in: CustomLinkUpdate) -> Optional[CustomLink]:
        link = self.repository.get(link_id)
        if not link or link.user_id != user_id:
            return None

        if link_in.slug and link_in.slug != link.slug:
            existing = self.repository.get_by_slug(link_in.slug)
            if existing:
                random_suffix = uuid.uuid4().hex[:4]
                link_in.slug = f"{link_in.slug}-{random_suffix}"

        return self.repository.update(link, link_in)

    def delete_link(self, link_id: int, user_id: int) -> bool:
        link = self.repository.get(link_id)
        if not link or link.user_id != user_id:
            return False

        self.repository.delete(link_id)
        return True

    def handle_redirect(self, slug: str) -> dict:
        """
        Handle redirect for a given slug.
        Returns dict with 'url' on success, or 'error' and 'status_code' on failure.
        """
        link = self.repository.get_by_slug(slug)
        if not link:
            return {"error": "Link não encontrado", "status_code": 404}

        if not link.is_active:
            return {"error": "Este link está desativado", "status_code": 403}

        if link.expires_at and link.expires_at < datetime.now(timezone.utc):
            return {"error": "Este link expirou", "status_code": 410}

        self.repository.increment_click_count(link)
        return {"url": link.original_url}
