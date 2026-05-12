"""
Endpoints do programa de afiliados (Indique & Ganhe).

- GET /affiliates/me          — saldo + histórico do afiliado autenticado
- GET /admin/affiliates/pending — lista agregada para o admin pagar manualmente
- POST /admin/commissions/pay  — admin marca comissões como pagas
"""
import logging
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user, require_admin
from app.core.config import settings
from app.db.session import get_db
from app.models.commission import Commission
from app.models.user import User
from app.repositories.commission_repository import CommissionRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.schemas.commission import (
    AffiliateSummaryResponse,
    CommissionResponse,
    MarkPaidRequest,
    MarkPaidResponse,
    PendingAffiliateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["affiliates"])


def _commission_to_response(c: Commission, referred_email: str) -> CommissionResponse:
    return CommissionResponse(
        id=c.id,
        referred_email=referred_email,
        amount=Decimal(c.amount),
        base_amount=Decimal(c.base_amount),
        rate=Decimal(c.rate),
        status=c.status,
        paid_at=c.paid_at,
        payment_reference=c.payment_reference,
        created_at=c.created_at,
    )


@router.get("/affiliates/me", response_model=AffiliateSummaryResponse)
def get_my_affiliate_summary(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = CommissionRepository(db)
    commissions = repo.get_by_referrer(current_user.id)

    pending = repo.sum_by_status(current_user.id, "pending")
    paid = repo.sum_by_status(current_user.id, "paid")
    referrals_count = repo.count_distinct_referred(current_user.id)

    # Active referrals: subscriptions ativas dos indicados
    sub_repo = SubscriptionRepository(db)
    referred_user_ids = list({c.referred_user_id for c in commissions})
    active_referrals_count = 0
    if referred_user_ids:
        for ref_id in referred_user_ids:
            sub = sub_repo.get_by_user_id(ref_id)
            if sub and sub.is_active:
                active_referrals_count += 1

    # Mapear emails dos referred (para mostrar quem é cada comissão)
    emails_by_id = {
        u.id: u.email
        for u in db.query(User).filter(User.id.in_(referred_user_ids)).all()
    } if referred_user_ids else {}

    base_url = (settings.FRONTEND_URL or "https://marketdash.com.br").rstrip("/")
    ref_link = f"{base_url}/?ref={current_user.id}"

    return AffiliateSummaryResponse(
        ref_link=ref_link,
        balance_pending=pending,
        total_paid=paid,
        referrals_count=referrals_count,
        active_referrals_count=active_referrals_count,
        commissions=[
            _commission_to_response(c, emails_by_id.get(c.referred_user_id, "—"))
            for c in commissions
        ],
    )


@router.get("/admin/affiliates/pending", response_model=List[PendingAffiliateResponse])
def get_pending_affiliates(
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = CommissionRepository(db).pending_aggregated()
    return [PendingAffiliateResponse(**r) for r in rows]


@router.post("/admin/commissions/pay", response_model=MarkPaidResponse)
def mark_commissions_paid(
    payload: MarkPaidRequest,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if not payload.commission_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="commission_ids vazio")
    if not payload.payment_reference.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="payment_reference obrigatório")

    paid_count, total = CommissionRepository(db).mark_paid_bulk(
        payload.commission_ids, payload.payment_reference.strip()
    )
    db.commit()
    return MarkPaidResponse(paid_count=paid_count, total_paid=total)
