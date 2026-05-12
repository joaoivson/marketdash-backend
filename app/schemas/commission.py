from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from pydantic import BaseModel


class CommissionResponse(BaseModel):
    id: int
    referred_email: str
    amount: Decimal
    base_amount: Decimal
    rate: Decimal
    status: str
    paid_at: Optional[datetime] = None
    payment_reference: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AffiliateSummaryResponse(BaseModel):
    ref_link: str
    balance_pending: Decimal
    total_paid: Decimal
    referrals_count: int
    active_referrals_count: int
    commissions: List[CommissionResponse]


class PendingAffiliateResponse(BaseModel):
    referrer_user_id: int
    name: Optional[str]
    email: str
    pix_key: Optional[str]
    total_pending: Decimal
    commissions_count: int
    commission_ids: List[int]


class MarkPaidRequest(BaseModel):
    commission_ids: List[int]
    payment_reference: str


class MarkPaidResponse(BaseModel):
    paid_count: int
    total_paid: Decimal
