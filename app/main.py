from fastapi import Depends, FastAPI, Request, status
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

# Webhook do Instagram — fora do /api/v1 porque a URL fica cadastrada no painel
# da Meta e não deve carregar versionamento de API interna.
# Rotas: /webhooks/instagram (handshake + comentários), /deauthorize, /data-deletion
from app.api.webhooks import instagram as instagram_webhook
app.include_router(instagram_webhook.router, prefix="/webhooks")


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


@app.get("/g/{slug}", response_class=HTMLResponse, include_in_schema=False)
@app.get("/g/preview/{slug}", response_class=HTMLResponse, include_in_schema=False)
def link_de_entrada(slug: str, request: Request, db: Session = Depends(get_db)):
    """
    Link de entrada da campanha de grupos (F6).

    Servido pelo BACKEND, não pelo frontend, por dois motivos que não são
    negociáveis: o crawler do WhatsApp não executa JS (a prévia customizada só
    existe com OG tags server-side) e o pixel do Facebook precisa disparar
    ANTES do redirecionamento para o convite.

    `/g/preview/{slug}` roteia igual, mas o clique nasce `is_teste` e não entra
    em métrica nenhuma — a afiliada testa o próprio link sem sujar o número.
    """
    from html import escape

    from app.services.campanha_link_service import (
        CampanhaLinkService, LinkInvalido, SemVaga,
    )

    is_preview = request.url.path.startswith("/g/preview/")
    servico = CampanhaLinkService(db)
    ip = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    try:
        link, convite = servico.rotear(
            slug, ip=ip,
            user_agent=request.headers.get("user-agent"),
            referer=request.headers.get("referer"),
            is_preview=is_preview,
        )
    except SemVaga:
        return HTMLResponse(status_code=200, content=_pagina_simples(
            "Vagas esgotadas",
            "Todos os grupos estão cheios no momento. Tente de novo mais tarde.",
        ))
    except LinkInvalido:
        return HTMLResponse(status_code=404, content=_pagina_simples(
            "Link indisponível", "Este link não está mais ativo."
        ))

    previa = servico.dados_da_previa(link)
    titulo = escape(previa["titulo"] or "", quote=True)
    descricao = escape(previa["descricao"] or "", quote=True)
    imagem = escape(previa["imagem"] or "", quote=True)
    destino = escape(convite, quote=True)

    eventos = link.pixel_eventos or {}
    pixel_js = ""
    if link.pixel_facebook_id:
        pixel_id = escape(link.pixel_facebook_id, quote=True)
        disparos = []
        if eventos.get("pageview", True):
            disparos.append("fbq('track','PageView');")
        if eventos.get("lead", True):
            disparos.append("fbq('track','Lead');")
        pixel_js = f"""<script>
!function(f,b,e,v,n,t,s){{if(f.fbq)return;n=f.fbq=function(){{n.callMethod?
n.callMethod.apply(n,arguments):n.queue.push(arguments)}};if(!f._fbq)f._fbq=n;
n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}}(window,
document,'script','https://connect.facebook.net/en_US/fbevents.js');
fbq('init','{pixel_id}');{''.join(disparos)}
</script>"""

    # 1,2s de folga para o pixel sair antes do redirect; o link também é
    # clicável, para quem tiver JS/refresh bloqueado.
    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="{titulo}">
<meta property="og:description" content="{descricao}">
<meta property="og:image" content="{imagem}">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">
<title>{titulo}</title>
{pixel_js}
<meta http-equiv="refresh" content="1;url={destino}">
</head>
<body style="font-family:system-ui,sans-serif;background:#0b1220;color:#e5e7eb;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center;padding:24px">
<p style="font-size:18px;margin:0 0 12px">Entrando no grupo…</p>
<a href="{destino}" style="color:#7CB8F2">Toque aqui se não abrir sozinho</a>
</div>
</body>
</html>"""
    return HTMLResponse(content=html)


def _pagina_simples(titulo: str, mensagem: str) -> str:
    from html import escape

    return f"""<!DOCTYPE html>
<html lang="pt-BR"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(titulo)}</title></head>
<body style="font-family:system-ui,sans-serif;background:#0b1220;color:#e5e7eb;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0">
<div style="text-align:center;padding:24px">
<h1 style="font-size:20px;margin:0 0 8px">{escape(titulo)}</h1>
<p style="color:#9ca3af;margin:0">{escape(mensagem)}</p>
</div></body></html>"""


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

