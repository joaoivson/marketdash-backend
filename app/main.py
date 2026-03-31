from fastapi import FastAPI, status, Depends
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.logging import configure_logging
from app.core.errors import register_exception_handlers
from app.api.v1.routes import router as api_v1_router
from app.db.base import init_db
from app.db.session import SessionLocal, get_db
from datetime import datetime, timezone
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
# max_age=3600 cacheia respostas de preflight por 1 hora, reduzindo chamadas duplicadas
# Usa get_cors_origins() para suportar FORCE_HTTP_FALLBACK em emergências
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.get_cors_origins() + ["http://localhost:8080", "http://localhost:5173", "http://127.0.0.1:8080", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "PATCH"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "Content-Type", "*"],  # Garantir que Content-Disposition seja exposto
    max_age=3600,  # Cache preflight por 1 hora
)

# GZip middleware for faster large responses
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Include routers (v1 only)
app.include_router(api_v1_router, prefix=settings.API_V1_STR)

# Rota alternativa para webhook Cakto (sem prefixo /api/v1)
# Permite que o webhook funcione em /cakto/webhook além de /api/v1/cakto/webhook
from app.api.v1.routes import cakto as cakto_v1
app.include_router(cakto_v1.router, prefix="/cakto", tags=["cakto"])


@app.get("/")
def root():
    """Root endpoint."""
    return {
        "message": "MarketDash Backend API",
        "version": "1.0.0",
        "docs": "/docs",
        "environment": settings.ENVIRONMENT
    }


@app.get("/c/{slug}/og", response_class=HTMLResponse, include_in_schema=False)
def capture_site_og(slug: str, db: Session = Depends(get_db)):
    """Serve HTML with OG meta tags for social media crawlers."""
    from html import escape
    from app.models.capture_site import CaptureSite
    site = db.query(CaptureSite).filter(
        CaptureSite.slug == slug,
        CaptureSite.is_active == True
    ).first()

    if not site:
        return HTMLResponse(status_code=404, content="<html><body>Not found</body></html>")

    title = escape(site.title or "", quote=True)
    description = escape(site.subtitle or "", quote=True)
    image = escape(site.image_url or "", quote=True)
    site_url = f"https://marketdash.com.br/c/{escape(slug, quote=True)}"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{description}">
<meta property="og:image" content="{image}">
<meta property="og:url" content="{site_url}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{description}">
<meta name="twitter:image" content="{image}">
<meta http-equiv="refresh" content="0;url={site_url}">
<title>{title}</title>
</head>
<body></body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/l/{slug}/og", response_class=HTMLResponse, include_in_schema=False)
def custom_link_og(slug: str, db: Session = Depends(get_db)):
    """Serve HTML with OG meta tags for social media crawlers on custom links."""
    from html import escape
    from app.models.custom_link import CustomLink
    link = db.query(CustomLink).filter(
        CustomLink.slug == slug,
        CustomLink.is_active == True
    ).first()

    if not link:
        return HTMLResponse(status_code=404, content="<html><body>Not found</body></html>")

    title = escape(link.name or "", quote=True)
    link_url = f"https://marketdash.com.br/l/{escape(slug, quote=True)}"

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta property="og:title" content="{title}">
<meta property="og:url" content="{link_url}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary">
<meta name="twitter:title" content="{title}">
<meta http-equiv="refresh" content="0;url={link_url}">
<title>{title}</title>
</head>
<body></body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/health")
def health_check():
    """
    Health check endpoint with database and service status.
    Returns detailed information about the application health.
    """
    health_status = {
        "status": "healthy",
        "environment": settings.ENVIRONMENT,
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        "database": "unknown",
        "redis": "not_configured"
    }
    
    # Check database connection
    try:
        db = SessionLocal()
        try:
            # Simple query to verify database connection
            db.execute(text("SELECT 1"))
            health_status["database"] = "connected"
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            health_status["database"] = "disconnected"
            health_status["status"] = "unhealthy"
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Database session creation failed: {e}")
        health_status["database"] = "error"
        health_status["status"] = "unhealthy"
    
    # Check Redis if configured
    if settings.REDIS_URL:
        try:
            import redis
            # Detectar se a URL já tem senha ou se precisamos adicionar
            redis_client = redis.from_url(settings.REDIS_URL, socket_timeout=2)
            redis_client.ping()
            health_status["redis"] = "connected"
        except ImportError:
            health_status["redis"] = "library_not_installed"
        except redis.exceptions.AuthenticationError:
            logger.warning("Redis health check: Autenticação necessária (verifique REDIS_URL)")
            health_status["redis"] = "auth_required"
        except Exception as e:
            logger.warning(f"Redis health check failed: {str(e)}")
            health_status["redis"] = "disconnected"
    
    # Return appropriate status code
    status_code = status.HTTP_200_OK if health_status["status"] == "healthy" else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        content=health_status,
        status_code=status_code
    )

