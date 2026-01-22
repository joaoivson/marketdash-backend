from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # Database (Supabase PostgreSQL)
    DATABASE_URL: str
    
    # Supabase Configuration
    SUPABASE_URL: Optional[str] = None
    SUPABASE_KEY: Optional[str] = None
    SUPABASE_SERVICE_KEY: Optional[str] = None
    
    # JWT
    JWT_SECRET: str
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRATION_HOURS: int = 24
    
    # App Configuration
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "DashAds Backend"
    ENVIRONMENT: str = "development"

    # Cache / Redis
    REDIS_URL: Optional[str] = None
    CACHE_TTL_SECONDS: int = 300

    # Cakto Integration
    CAKTO_API_BASE: str = "https://api.cakto.com.br"
    CAKTO_CLIENT_ID: Optional[str] = None
    CAKTO_CLIENT_SECRET: Optional[str] = None
    CAKTO_SUBSCRIPTION_PRODUCT_IDS: Optional[str] = None  # CSV of product IDs
    CAKTO_ENFORCE_SUBSCRIPTION: bool = False
    CAKTO_WEBHOOK_SECRET: Optional[str] = None
    
    # CORS (for production)
    CORS_ORIGINS: list[str] = [
        # Development
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        # Production
        "https://marketdash.com.br",
        "http://marketdash.com.br",
        "https://api.marketdash.com.br",
        "http://api.marketdash.com.br",
        # Homologation
        "https://hml.marketdash.com.br",
        "http://hml.marketdash.com.br",
        "https://api.hml.marketdash.com.br",
        "http://api.hml.marketdash.com.br",
    ]
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

