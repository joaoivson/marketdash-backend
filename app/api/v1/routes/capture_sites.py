from typing import List
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.db.session import get_db
from app.models.user import User
from app.schemas.capture_site import CaptureSiteCreate, CaptureSiteUpdate, CaptureSiteResponse, SlugCheckResponse
from app.repositories.capture_site_repository import CaptureSiteRepository
from app.services.capture_site_service import CaptureSiteService

router = APIRouter(tags=["capture_sites"])

def get_service(db: Session = Depends(get_db)):
    repo = CaptureSiteRepository(db)
    return CaptureSiteService(repo)

@router.get("/check-slug", response_model=SlugCheckResponse)
def check_slug(
    slug: str = Query(..., description="The slug to check"),
    service: CaptureSiteService = Depends(get_service)
):
    """Check if a slug is available for a capture page."""
    return service.check_slug(slug)

@router.get("/public/{slug}", response_model=CaptureSiteResponse)
def get_public_site(
    slug: str,
    service: CaptureSiteService = Depends(get_service)
):
    """Get a capture site by slug (Public endpoint)."""
    site = service.get_site_by_slug(slug)
    if not site:
        raise HTTPException(status_code=404, detail="Página não encontrada")
    if not site.is_active:
        raise HTTPException(status_code=403, detail="Esta página está temporariamente desativada")
    return site

@router.get("", response_model=List[CaptureSiteResponse])
def list_user_sites(
    current_user: User = Depends(require_active_subscription),
    service: CaptureSiteService = Depends(get_service)
):
    """List all capture sites for the authenticated user."""
    return service.get_user_sites(current_user.id)

@router.post("", response_model=CaptureSiteResponse, status_code=status.HTTP_201_CREATED)
def create_site(
    site_in: CaptureSiteCreate,
    current_user: User = Depends(require_active_subscription),
    service: CaptureSiteService = Depends(get_service)
):
    """Create a new capture site."""
    try:
        return service.create_site(current_user.id, site_in)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))

@router.get("/{site_id}", response_model=CaptureSiteResponse)
def get_site(
    site_id: int,
    current_user: User = Depends(require_active_subscription),
    service: CaptureSiteService = Depends(get_service)
):
    """Get a specific capture site by ID."""
    site = service.get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="Página não encontrada")
    if site.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Não autorizado")
    return site

@router.put("/{site_id}", response_model=CaptureSiteResponse)
def update_site(
    site_id: int,
    site_in: CaptureSiteUpdate,
    current_user: User = Depends(require_active_subscription),
    service: CaptureSiteService = Depends(get_service)
):
    """Update a capture site."""
    updated_site = service.update_site(site_id, current_user.id, site_in)
    if not updated_site:
        raise HTTPException(status_code=404, detail="Página não encontrada ou não autorizada")
    return updated_site

@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_site(
    site_id: int,
    current_user: User = Depends(require_active_subscription),
    service: CaptureSiteService = Depends(get_service)
):
    """Delete a capture site."""
    deleted = service.delete_site(site_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Página não encontrada ou não autorizada")
    return None
