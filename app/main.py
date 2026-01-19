from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.errors import register_exception_handlers
from app.api.v1.routes import router as api_v1_router
from app.db.base import init_db
import logging

# Configure logging
configure_logging()
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Backend SaaS para análise de dados com ingestão de CSV",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc"
)

register_exception_handlers(app)


@app.on_event("startup")
async def startup_event():
    """Initialize database on startup."""
    logger.info("Initializing database...")
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise

# CORS middleware (using settings)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GZip middleware for faster large responses
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Include routers (v1 only)
app.include_router(api_v1_router, prefix=settings.API_V1_STR)


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "message": "DashAds Backend API",
        "version": "1.0.0",
        "docs": "/docs",
        "environment": settings.ENVIRONMENT
    }


@app.get("/health")
def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}

