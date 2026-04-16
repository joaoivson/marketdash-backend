from sqlalchemy.orm import declarative_base

# Create declarative base
Base = declarative_base()

# Models are imported in app/models/__init__.py to avoid circular imports


def _apply_safe_migrations(engine, logger):
    """Add missing columns to existing tables. Each statement is idempotent."""
    migrations = [
        "ALTER TABLE capture_sites ADD COLUMN IF NOT EXISTS facebook_pixel_id VARCHAR",
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
    from app.models import User, Dataset, DatasetRow, Subscription, AdSpend, ClickRow, Job, JobChunk, CaptureSite, CustomLink, PageEvent  # noqa: F401
    from app.models.user_settings import UserSettings  # noqa: F401
    
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

