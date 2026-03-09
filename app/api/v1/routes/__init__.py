from fastapi import APIRouter

from . import (
    ad_spends,
    auth,
    cakto,
    capture_sites,
    clicks,
    dashboard,
    datasets,
    feedback,
    jobs,
    subscription,
)

router = APIRouter()

router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(cakto.router, prefix="/cakto", tags=["cakto"])
router.include_router(capture_sites.router, prefix="/capturas", tags=["capture_sites"])
router.include_router(ad_spends.router, prefix="/ad-spends", tags=["ad_spends"])
router.include_router(clicks.router, prefix="/clicks", tags=["clicks"])
router.include_router(dashboard.router, prefix="/dashboard", tags=["dashboard"])
router.include_router(datasets.router, prefix="/datasets", tags=["datasets"])
router.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
router.include_router(jobs.router, prefix="/jobs", tags=["jobs"])
router.include_router(subscription.router, prefix="/subscriptions", tags=["subscriptions"])
