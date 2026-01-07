from sqlalchemy.orm import declarative_base

# Create declarative base
Base = declarative_base()

# Models are imported in app/models/__init__.py to avoid circular imports


def init_db():
    """Initialize database tables."""
    # Import engine here to avoid circular import
    from app.db.session import engine
    from sqlalchemy import text
    import time
    import logging
    
    # Import all models to register them with Base.metadata
    # This must happen before create_all()
    from app.models import User, Dataset, DatasetRow, Subscription  # noqa: F401
    
    logger = logging.getLogger(__name__)
    
    # Retry logic to wait for database to be ready
    max_retries = 30
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            # Test connection
            with engine.begin() as conn:
                conn.execute(text("SELECT 1"))
                # Ensure optional new columns exist (backward compatible)
                conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS name VARCHAR(255)"))
                conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS cpf_cnpj VARCHAR(32)"))
                conn.execute(text("ALTER TABLE IF EXISTS users ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ"))
                conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS users_cpf_cnpj_key ON users (cpf_cnpj) WHERE cpf_cnpj IS NOT NULL"))
            # If connection successful, create tables (no-op for existing)
            Base.metadata.create_all(bind=engine)
            logger.info("Database tables created/updated successfully")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database not ready, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise

