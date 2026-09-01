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
from app.models.custom_link_event import CustomLinkEvent
from app.models.page_event import PageEvent
from app.models.commission import Commission
from app.models.user_settings import UserSettings
from app.models.shopee_integration import ShopeeIntegration
from app.models.facebook_integration import FacebookIntegration
from app.models.instagram_automation import (
    InstagramConnection,
    InstagramAutomation,
    InstagramEvent,
)
from app.models.campaign import Campaign, CampaignDailyInsight, CampaignPlatformDailyInsight
from app.models.kiwify_plan_product import KiwifyPlanProduct
from app.models.subscription_event import SubscriptionEvent
from app.models.user_login import UserLogin
from app.models.expense import Expense
from app.models.admin_client_note import AdminClientNote
from app.models.page_view import PageView
from app.models.sync_error_log import SyncErrorLog
from app.models.sync_run import SyncRun
from app.models.whatsapp import WhatsappOptin, WhatsappEnvio
from app.models.whatsapp_proxies import WhatsappProxy
from app.models.waha_servidores import WahaServidor
from app.models.whatsapp_grupos import WhatsappInstancia, WhatsappGrupo, WhatsappGrupoInstancia
from app.models.campanha_grupos import Campanha, CampanhaGrupo
from app.models.integracao import Integracao
from app.models.campanha_anuncio import CampanhaAnuncio
from app.models.campanha_link import (
    CampanhaLink, CampanhaLinkEvento, GrupoEvento, GrupoSnapshot,
)
from app.models.roteiro import (
    Roteiro, RoteiroPasso, RoteiroExecucao, RoteiroMensagem,
    TemplateMensagem, TemplateVariacao, BlacklistNumero,
)
from app.models.monitoramento import Monitoramento, MonitoramentoCaptura
from app.models.conexao_convite import ConexaoConvite

__all__ = [
    "User", "Dataset", "DatasetRow", "Subscription", "AdSpend", "ClickRow",
    "Job", "JobChunk", "CaptureSite", "CustomLink", "CustomLinkEvent", "PageEvent", "Commission",
    "UserSettings", "ShopeeIntegration", "FacebookIntegration",
    "InstagramConnection", "InstagramAutomation", "InstagramEvent",
    "Campaign", "CampaignDailyInsight", "CampaignPlatformDailyInsight", "KiwifyPlanProduct",
    "SubscriptionEvent", "UserLogin", "Expense", "AdminClientNote", "PageView", "SyncErrorLog",
    "SyncRun",
    "WhatsappOptin", "WhatsappEnvio",
    "WhatsappInstancia", "WhatsappGrupo", "WhatsappGrupoInstancia", "WhatsappProxy",
    "Campanha", "CampanhaGrupo", "Integracao",
    "CampanhaLink", "CampanhaLinkEvento", "GrupoEvento", "GrupoSnapshot",
    "CampanhaAnuncio",
    "Roteiro", "RoteiroPasso", "RoteiroExecucao", "RoteiroMensagem",
    "TemplateMensagem", "TemplateVariacao", "BlacklistNumero", "Monitoramento", "MonitoramentoCaptura", "ConexaoConvite",
]

