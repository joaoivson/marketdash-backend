from fastapi import APIRouter

from app.api.v1.routes import auth, datasets, dashboard, ad_spends, cakto, subscription, clicks

router = APIRouter()
router.include_router(auth.router, prefix="/auth")
router.include_router(datasets.router, prefix="/datasets")
router.include_router(dashboard.router, prefix="/dashboard")
router.include_router(ad_spends.router, prefix="/ad_spends")
router.include_router(cakto.router, prefix="/cakto")
router.include_router(subscription.router, prefix="/subscription")
router.include_router(clicks.router, prefix="/clicks")
