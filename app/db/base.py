from sqlalchemy.orm import declarative_base

# Create declarative base
Base = declarative_base()

# Models are imported in app/models/__init__.py to avoid circular imports


def _apply_safe_migrations(engine, logger):
    """Add missing columns to existing tables. Each statement is idempotent."""
    migrations = [
        "ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS facebook_pixel_id VARCHAR",
        "ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_json TEXT",
        # 060 (grupos F3): sem esta coluna, TODA query de UserSettings quebra
        # se o código chegar antes da migration — rede extra do protocolo.
        "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS whatsapp_envio_config JSONB",
        # 065 (grupos F7): Leads do Meta por dia.
        "ALTER TABLE campaign_daily_insights ADD COLUMN IF NOT EXISTS leads INTEGER",
        # 067 (grupos, item 17): a tabela nasceu na 060 e ganhou colunas depois.
        # `create_all` NÃO altera tabela existente — sem isto, todo ambiente que
        # já tinha `blacklist_numeros` quebra ao consultá-la.
        "ALTER TABLE blacklist_numeros ADD COLUMN IF NOT EXISTS numero_mascarado VARCHAR(24)",
        "ALTER TABLE blacklist_numeros ADD COLUMN IF NOT EXISTS "
        "remover_dos_grupos BOOLEAN NOT NULL DEFAULT TRUE",
        # 068 (proxy por sessão): `whatsapp_instancias` já existe em todo
        # ambiente, e `create_all` NÃO adiciona coluna a tabela existente —
        # sem isto, qualquer query de instância quebra com UndefinedColumn
        # se o deploy chegar antes da migration.
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_id INTEGER",
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_fixado_em TIMESTAMPTZ",
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
        "proxy_trocas INTEGER NOT NULL DEFAULT 0",
    ]
    from sqlalchemy import text
    try:
        with engine.begin() as conn:
            for stmt in migrations:
                conn.execute(text(stmt))
        logger.info("Safe migrations applied successfully")
    except Exception as e:
        logger.warning(f"Safe migrations skipped: {e}")


def init_db():
    """Initialize database tables."""
    # Import engine here to avoid circular import
    from app.db.session import engine
    from sqlalchemy import text
    import time
    import logging
    
    # Import all models to register them with Base.metadata
    # This must happen before create_all()
    from app.models import User, Dataset, DatasetRow, Subscription, AdSpend, ClickRow, Job, JobChunk, CaptureSite, CustomLink, CustomLinkEvent, PageEvent  # noqa: F401
    from app.models.user_settings import UserSettings  # noqa: F401
    from app.models.shopee_integration import ShopeeIntegration  # noqa: F401
    from app.models.facebook_integration import FacebookIntegration  # noqa: F401
    from app.models.campaign import Campaign, CampaignDailyInsight  # noqa: F401
    
    logger = logging.getLogger(__name__)
    
    # Retry logic to wait for database to be ready
    max_retries = 30
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            # Test connection
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
                logger.info("Database connection successful")
            # If connection successful, create tables (no-op for existing)
            Base.metadata.create_all(bind=engine)
            # Add missing columns to existing tables (create_all doesn't do this)
            _apply_safe_migrations(engine, logger)
            logger.info("Database tables created/updated successfully")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database not ready, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries}): {e}")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise

