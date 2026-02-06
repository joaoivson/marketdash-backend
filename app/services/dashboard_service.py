from sqlalchemy.orm import Session
from sqlalchemy import func, and_, or_
from datetime import date, datetime
from typing import Optional, List
from decimal import Decimal
import hashlib

from app.models.dataset_row import DatasetRow
from app.schemas.dashboard import (
    DashboardFilters,
    KPIs,
    PeriodAggregation,
    ProductAggregation,
    DashboardResponse
)
from app.core.cache import cache_get, cache_set, cache_delete_prefix


class DashboardService:
    """Service for dashboard analytics and aggregations."""

    @staticmethod
    def build_filters(
        db: Session,
        user_id: int,
        filters: DashboardFilters
    ) -> List:
        """Build SQLAlchemy filter conditions based on dashboard filters."""
        conditions = [DatasetRow.user_id == user_id]
        
        if filters.start_date:
            conditions.append(DatasetRow.date >= filters.start_date)
        
        if filters.end_date:
            conditions.append(DatasetRow.date <= filters.end_date)
        
        if filters.product:
            conditions.append(DatasetRow.product.ilike(f"%{filters.product}%"))
        
        if filters.min_value is not None:
            conditions.append(
                or_(
                    DatasetRow.revenue >= filters.min_value,
                    DatasetRow.cost >= filters.min_value,
                    DatasetRow.commission >= filters.min_value,
                    DatasetRow.profit >= filters.min_value
                )
            )
        
        if filters.max_value is not None:
            conditions.append(
                or_(
                    DatasetRow.revenue <= filters.max_value,
                    DatasetRow.cost <= filters.max_value,
                    DatasetRow.commission <= filters.max_value,
                    DatasetRow.profit <= filters.max_value
                )
            )
        
        return conditions

    @staticmethod
    def get_kpis(
        db: Session,
        user_id: int,
        filters: DashboardFilters
    ) -> KPIs:
        """Calculate KPIs for the dashboard."""
        conditions = DashboardService.build_filters(db, user_id, filters)
        
        result = db.query(
            func.sum(DatasetRow.revenue).label('total_revenue'),
            func.sum(DatasetRow.cost).label('total_cost'),
            func.sum(DatasetRow.commission).label('total_commission'),
            func.sum(DatasetRow.profit).label('total_profit'),
            func.count(DatasetRow.id).label('total_rows')
        ).filter(and_(*conditions)).first()
        
        return KPIs(
            total_revenue=float(result.total_revenue or 0),
            total_cost=float(result.total_cost or 0),
            total_commission=float(result.total_commission or 0),
            total_profit=float(result.total_profit or 0),
            total_rows=int(result.total_rows or 0)
        )

    @staticmethod
    def get_period_aggregations(
        db: Session,
        user_id: int,
        filters: DashboardFilters
    ) -> List[PeriodAggregation]:
        """Get aggregations grouped by date."""
        conditions = DashboardService.build_filters(db, user_id, filters)
        
        results = db.query(
            DatasetRow.date.label('period'),
            func.sum(DatasetRow.revenue).label('revenue'),
            func.sum(DatasetRow.cost).label('cost'),
            func.sum(DatasetRow.commission).label('commission'),
            func.sum(DatasetRow.profit).label('profit'),
            func.count(DatasetRow.id).label('row_count')
        ).filter(
            and_(*conditions)
        ).group_by(
            DatasetRow.date
        ).order_by(
            DatasetRow.date
        ).all()
        
        return [
            PeriodAggregation(
                period=str(result.period),
                revenue=float(result.revenue or 0),
                cost=float(result.cost or 0),
                commission=float(result.commission or 0),
                profit=float(result.profit or 0),
                row_count=int(result.row_count or 0)
            )
            for result in results
        ]

    @staticmethod
    def get_product_aggregations(
        db: Session,
        user_id: int,
        filters: DashboardFilters
    ) -> List[ProductAggregation]:
        """Get aggregations grouped by product."""
        conditions = DashboardService.build_filters(db, user_id, filters)
        
        results = db.query(
            DatasetRow.product.label('product'),
            func.sum(DatasetRow.revenue).label('revenue'),
            func.sum(DatasetRow.cost).label('cost'),
            func.sum(DatasetRow.commission).label('commission'),
            func.sum(DatasetRow.profit).label('profit'),
            func.count(DatasetRow.id).label('row_count')
        ).filter(
            and_(*conditions)
        ).group_by(
            DatasetRow.product
        ).order_by(
            func.sum(DatasetRow.profit).desc()
        ).all()
        
        return [
            ProductAggregation(
                product=result.product,
                revenue=float(result.revenue or 0),
                cost=float(result.cost or 0),
                commission=float(result.commission or 0),
                profit=float(result.profit or 0),
                row_count=int(result.row_count or 0)
            )
            for result in results
        ]

    @staticmethod
    def get_dashboard(
        db: Session,
        user_id: int,
        filters: DashboardFilters
    ) -> DashboardResponse:
        """Get complete dashboard data with KPIs and aggregations."""
        # Generate cache key based on user_id and filters
        filters_dict = filters.dict(exclude_none=True)
        filters_hash = hashlib.md5(str(sorted(filters_dict.items())).encode()).hexdigest()
        cache_key = f"dashboard:user:{user_id}:filters:{filters_hash}"
        
        # Try to get from cache
        cached_data = cache_get(cache_key)
        if cached_data:
            return DashboardResponse(**cached_data)
        
        # Cache miss - query database
        kpis = DashboardService.get_kpis(db, user_id, filters)
        period_aggregations = DashboardService.get_period_aggregations(db, user_id, filters)
        product_aggregations = DashboardService.get_product_aggregations(db, user_id, filters)
        
        response = DashboardResponse(
            kpis=kpis,
            period_aggregations=period_aggregations,
            product_aggregations=product_aggregations
        )
        
        # Cache the response (5 minutes TTL)
        cache_set(cache_key, response.dict(), ttl=300)
        
        return response
    
    @staticmethod
    def invalidate_user_cache(user_id: int) -> None:
        """Invalidate all cached dashboard data for a user."""
        cache_delete_prefix(f"dashboard:user:{user_id}:")

