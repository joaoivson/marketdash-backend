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
    from app.models import User, Dataset, DatasetRow, Subscription, AdSpend, ClickRow  # noqa: F401
    
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
            logger.info("Database tables created/updated successfully")
            return
        except Exception as e:
            if attempt < max_retries - 1:
                logger.warning(f"Database not ready, retrying in {retry_delay}s... (attempt {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
            else:
                logger.error(f"Failed to connect to database after {max_retries} attempts: {e}")
                raise

