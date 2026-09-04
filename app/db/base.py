from sqlalchemy.orm import declarative_base

# Create declarative base
Base = declarative_base()

# Models are imported in app/models/__init__.py to avoid circular imports


def _apply_safe_migrations(engine, logger):
    """Add missing columns to existing tables. Each statement is idempotent."""
    migrations = [
        "ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS facebook_pixel_id VARCHAR",
        "ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_json TEXT",
        # 075 (Facebook §4.2): nomes das contas selecionadas — a tabela já existe
        # em todo ambiente e `create_all` NÃO adiciona coluna, mesmo padrão do
        # ad_accounts_json acima.
        "ALTER TABLE facebook_integrations ADD COLUMN IF NOT EXISTS ad_accounts_names_json TEXT",
        # 060 (grupos F3): sem esta coluna, TODA query de UserSettings quebra
        # se o código chegar antes da migration — rede extra do protocolo.
        "ALTER TABLE user_settings ADD COLUMN IF NOT EXISTS whatsapp_envio_config JSONB",
        # 065 (grupos F7): Leads do Meta por dia.
        "ALTER TABLE campaign_daily_insights ADD COLUMN IF NOT EXISTS leads INTEGER",
        # 068 (proxy por sessão): `whatsapp_instancias` já existe em todo
        # ambiente, e `create_all` NÃO adiciona coluna a tabela existente —
        # sem isto, qualquer query de instância quebra com UndefinedColumn
        # se o deploy chegar antes da migration.
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_id INTEGER",
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS proxy_fixado_em TIMESTAMPTZ",
        "ALTER TABLE whatsapp_instancias ADD COLUMN IF NOT EXISTS "
        "proxy_trocas INTEGER NOT NULL DEFAULT 0",
        # 074 (grupos, toggle Ativo): `ativado` é a escolha da USUÁRIA — eixo
        # separado de `ativo`, que é lifecycle do sync (todo sync revive com
        # ativo=True). A tabela já existe em todo ambiente e `create_all` NÃO
        # adiciona coluna — sem isto, GET /grupos quebra antes da migration.
        "ALTER TABLE whatsapp_grupos ADD COLUMN IF NOT EXISTS "
        "ativado BOOLEAN NOT NULL DEFAULT FALSE",
        # 076 (assinatura 10.2): compra de tier menor com maior vigente fica
        # pendente em vez de rebaixar na hora. `subscriptions` existe em todo
        # ambiente e `create_all` NÃO adiciona coluna — sem isto, qualquer
        # leitura de Subscription quebra se o deploy chegar antes da migration.
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_plan VARCHAR",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_periodo VARCHAR(32)",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS pending_vence_em TIMESTAMPTZ",
        "ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS "
        "pending_provider_transaction_id VARCHAR",
        # 079 (campanhas de grupos): as duas tabelas já existem em hml, e
        # `create_all` NÃO adiciona coluna — sem isto, qualquer leitura de
        # Campanha ou GrupoEvento quebra se o deploy chegar antes da migration.
        # (`campanha_numeros` é tabela NOVA: essa o create_all cria — e é por
        # isso que a 079 precisa chegar antes, para nascer com RLS.)
        "ALTER TABLE campanhas ADD COLUMN IF NOT EXISTS limite_participantes INTEGER",
        "ALTER TABLE grupo_eventos ADD COLUMN IF NOT EXISTS identificador VARCHAR(64)",
        "ALTER TABLE grupo_eventos ADD COLUMN IF NOT EXISTS "
        "identificador_tipo VARCHAR(12)",
        # 080 (Cheio × Aberto): `campanha_grupos` já existe em todo ambiente e
        # `create_all` NÃO adiciona coluna — sem isto, QUALQUER leitura de
        # vínculo (a aba Grupos inteira, e o roteamento do /g) quebra se o
        # deploy chegar antes da migration. As tabelas `grupo_participantes` e
        # `campanha_sub_ids` são NOVAS: essas o create_all cria — e é por isso
        # que a 080 precisa chegar antes, para nascerem com RLS.
        "ALTER TABLE campanha_grupos ADD COLUMN IF NOT EXISTS cheio_override BOOLEAN",
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

