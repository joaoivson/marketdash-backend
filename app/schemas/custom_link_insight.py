from typing import List, Optional
from pydantic import BaseModel


class LinkInsightSeriesPoint(BaseModel):
    label: str
    value: int


class LinkInsightResponse(BaseModel):
    total_clicks: int
    last_click_at: Optional[str] = None
    avg_per_day: float
    granularity: str
    series: List[LinkInsightSeriesPoint]
    series_started_at: Optional[str] = None
