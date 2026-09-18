from sqlalchemy.orm import declarative_base

# Create declarative base
Base = declarative_base()

# Models are imported in app/models/__init__.py to avoid circular imports


def _importar_modelos() -> None:
    """Registra todos os modelos em Base.metadata (necessário para create_all)."""
    from app.models import User, Dataset, DatasetRow, Subscription, AdSpend, ClickRow, Job, JobChunk, CaptureSite, CustomLink, CustomLinkEvent, PageEvent  # noqa: F401
    from app.models.user_settings import UserSettings  # noqa: F401
    from app.models.shopee_integration import ShopeeIntegration  # noqa: F401
    from app.models.facebook_integration import FacebookIntegration  # noqa: F401
    from app.models.campaign import Campaign, CampaignDailyInsight  # noqa: F401


def init_db():
    """Verifica a conexão com o banco no startup.

    Incidente de 18/09/2026: este startup rodava `create_all` (dezenas de
    consultas ao catálogo) mais dois `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    a CADA boot da API e do worker. Com o healthcheck do Coolify reiniciando o
    container porque o banco estava lento, foram 20 execuções em 24h — cada
    `ALTER TABLE` pega lock exclusivo na tabela, mesmo sem alterar nada, e
    bloqueava tudo que usava `capture_sites` e `facebook_integrations` num
    banco já sufocado.

    Agora o startup só confirma que o banco responde. Schema é migration
    (`migrations/*.sql`); as duas colunas viraram a 087. `create_all` fica
    atrás de `DB_SCHEMA_NO_STARTUP=true`, para dev/test subir do zero.
    """
    from app.db.session import engine
    from app.core.config import settings
    from sqlalchemy import text
    import time
    import logging

    logger = logging.getLogger(__name__)

    max_retries = 30
    retry_delay = 2

    for attempt in range(max_retries):
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Database connection successful")
            if settings.DB_SCHEMA_NO_STARTUP:
                _importar_modelos()
                Base.metadata.create_all(bind=engine)
                logger.info("Database tables created (DB_SCHEMA_NO_STARTUP=true)")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database not ready, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise
