from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_active_subscription
from app.db.session import get_db
from app.schemas.dashboard import DashboardFilters, DashboardResponse
from app.services.dashboard_service import DashboardService

router = APIRouter(tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    start_date: Optional[date] = Query(None, description="Data inicial (YYYY-MM-DD)"),
    end_date: Optional[date] = Query(None, description="Data final (YYYY-MM-DD)"),
    product: Optional[str] = Query(None, description="Filtrar por produto (busca parcial)"),
    min_value: Optional[float] = Query(None, description="Valor mínimo"),
    max_value: Optional[float] = Query(None, description="Valor máximo"),
    current_user=Depends(require_active_subscription),
    db: Session = Depends(get_db),
):
    filters = DashboardFilters(
        start_date=start_date,
        end_date=end_date,
        product=product,
        min_value=min_value,
        max_value=max_value,
    )
    return DashboardService.get_dashboard(db=db, user_id=current_user.id, filters=filters)
