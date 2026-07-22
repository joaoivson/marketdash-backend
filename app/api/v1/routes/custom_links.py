from typing import List
from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription, require_plan
from app.db.session import get_db
from app.models.user import User
from app.schemas.custom_link import CustomLinkCreate, CustomLinkUpdate, CustomLinkResponse, SlugCheckResponse
from app.schemas.custom_link_insight import LinkInsightResponse
from app.repositories.custom_link_repository import CustomLinkRepository
from app.services.custom_link_service import CustomLinkService

router = APIRouter(tags=["custom_links"])

require_pro = require_plan("pro")


def get_service(db: Session = Depends(get_db)):
    repo = CustomLinkRepository(db)
    return CustomLinkService(repo)


@router.get("/check-slug", response_model=SlugCheckResponse)
def check_slug(
    slug: str = Query(..., description="The slug to check"),
    service: CustomLinkService = Depends(get_service)
):
    """Check if a slug is available for a custom link."""
    return service.check_slug(slug)


@router.get("/r/{slug}")
def redirect_link(
    slug: str,
    request: Request,
    service: CustomLinkService = Depends(get_service)
):
    """Public endpoint - redirect to the original URL.
    Links continuam redirecionando mesmo em downgrade (Essencial) — regra de negócio.
    """
    user_agent = request.headers.get("user-agent")
    purpose = (
        request.headers.get("purpose")
        or request.headers.get("sec-purpose")
        or request.headers.get("x-moz")
        or ""
    )
    forwarded = request.headers.get("x-forwarded-for", "")
    ip = forwarded.split(",")[0].strip() if forwarded else (
        request.client.host if request.client else None
    )

    result = service.handle_redirect(
        slug, ip=ip, user_agent=user_agent, purpose=purpose
    )

    if "error" in result:
        raise HTTPException(status_code=result["status_code"], detail=result["error"])

    return RedirectResponse(url=result["url"], status_code=302)


@router.get("", response_model=List[CustomLinkResponse])
def list_user_links(
    current_user: User = Depends(require_pro),
    service: CustomLinkService = Depends(get_service)
):
    """List all custom links for the authenticated user."""
    return service.get_user_links(current_user.id)


@router.post("", response_model=CustomLinkResponse, status_code=status.HTTP_201_CREATED)
def create_link(
    link_in: CustomLinkCreate,
    current_user: User = Depends(require_pro),
    service: CustomLinkService = Depends(get_service)
):
    """Create a new custom link."""
    try:
        return service.create_link(current_user.id, link_in)
    except ValueError as e:
        msg = str(e)
        if "PLANO_INSUFICIENTE" in msg:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "PLANO_INSUFICIENTE", "message": msg},
            )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=msg)


@router.get("/{link_id}/insight", response_model=LinkInsightResponse)
def get_link_insight(
    link_id: int,
    granularity: str = Query("day", description="Granularidade da série: day ou month"),
    current_user: User = Depends(require_pro),
    service: CustomLinkService = Depends(get_service)
):
    """Insight de cliques de um link: KPIs + série temporal (day=14 dias, month=6 meses)."""
    if granularity not in ("day", "month"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="granularity deve ser 'day' ou 'month'",
        )

    insight = service.get_insight(link_id, current_user.id, granularity)
    if insight is None:
        raise HTTPException(status_code=404, detail="Link não encontrado ou não autorizado")
    return insight


@router.put("/{link_id}", response_model=CustomLinkResponse)
def update_link(
    link_id: int,
    link_in: CustomLinkUpdate,
    current_user: User = Depends(require_pro),
    service: CustomLinkService = Depends(get_service)
):
    """Update a custom link."""
    updated_link = service.update_link(link_id, current_user.id, link_in)
    if not updated_link:
        raise HTTPException(status_code=404, detail="Link não encontrado ou não autorizado")
    return updated_link


@router.delete("/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_link(
    link_id: int,
    current_user: User = Depends(require_pro),
    service: CustomLinkService = Depends(get_service)
):
    """Delete a custom link."""
    deleted = service.delete_link(link_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Link não encontrado ou não autorizado")
    return None
