# Import all models so SQLAlchemy mapper resolve relationships por string.
# IMPORTANTE: o User tem relationship("UserSettings", ...). Sem o import abaixo
# o mapper quebra ao usar em workers que importem so `from app.models import ...`.
from app.models.user import User
from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.models.subscription import Subscription
from app.models.ad_spend import AdSpend
from app.models.click_row import ClickRow
from app.models.job import Job, JobChunk
from app.models.capture_site import CaptureSite
from app.models.custom_link import CustomLink
from app.models.page_event import PageEvent
from app.models.commission import Commission
from app.models.user_settings import UserSettings
from app.models.shopee_integration import ShopeeIntegration

__all__ = [
    "User", "Dataset", "DatasetRow", "Subscription", "AdSpend", "ClickRow",
    "Job", "JobChunk", "CaptureSite", "CustomLink", "PageEvent", "Commission",
    "UserSettings", "ShopeeIntegration",
]

