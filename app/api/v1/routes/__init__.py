from fastapi import APIRouter

from app.core.config import settings
from . import auth, datasets, dashboard, ad_spends, cakto, subscription, clicks, feedback, capture_sites, uploads, custom_links, shopee, page_events, kiwify, payment, user_settings, internal, affiliates

router = APIRouter()
router.include_router(auth.router, prefix="/auth")
router.include_router(datasets.router, prefix="/datasets")
router.include_router(dashboard.router, prefix="/dashboard")
router.include_router(ad_spends.router, prefix="/ad_spends")
router.include_router(cakto.router, prefix="/cakto")
router.include_router(kiwify.router, prefix="/kiwify")
router.include_router(payment.router, prefix="/payment")
router.include_router(subscription.router, prefix="/subscription")
router.include_router(clicks.router, prefix="/clicks")
router.include_router(feedback.router, prefix="/feedback")
router.include_router(capture_sites.router, prefix="/capturas", tags=["capture_sites"])
router.include_router(uploads.router, prefix="/uploads", tags=["uploads"])
router.include_router(custom_links.router, prefix="/links", tags=["custom_links"])
router.include_router(shopee.router, prefix="/shopee")
router.include_router(page_events.router, prefix="/events", tags=["events"])
router.include_router(user_settings.router, prefix="/settings", tags=["settings"])
router.include_router(internal.router, prefix="/internal", tags=["internal"])
router.include_router(affiliates.router)  # rotas: /affiliates/me, /admin/affiliates/pending, /admin/commissions/pay

if settings.USE_JOBS_PIPELINE:
    from . import jobs
    router.include_router(jobs.router, prefix="/jobs")
