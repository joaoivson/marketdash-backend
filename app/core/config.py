from pydantic_settings import BaseSettings
from pydantic import model_validator
from typing import Optional, Dict


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
    PROJECT_NAME: str = "MarketDash Backend"
    ENVIRONMENT: str = "development"

    # Cache / Redis
    REDIS_URL: Optional[str] = None
    REDIS_PASSWORD: Optional[str] = None
    CACHE_TTL_SECONDS: int = 300

    @model_validator(mode='after')
    def assemble_redis_url(self) -> 'Settings':
        if self.REDIS_PASSWORD and self.REDIS_URL:
            import urllib.parse
            # Se a URL não tem senha mas temos REDIS_PASSWORD, injetamos
            if "@" not in self.REDIS_URL and "redis://" in self.REDIS_URL:
                # URL Encode a senha para garantir que caracteres especiais não quebrem a URL
                encoded_pwd = urllib.parse.quote_plus(self.REDIS_PASSWORD)
                # Formato: redis://:PASSWORD@HOST:PORT/DB
                self.REDIS_URL = self.REDIS_URL.replace("redis://", f"redis://:{encoded_pwd}@", 1)
        return self

    # Cakto Integration
    CAKTO_API_BASE: str = "https://api.cakto.com.br"
    CAKTO_CLIENT_ID: Optional[str] = None
    CAKTO_CLIENT_SECRET: Optional[str] = None
    # IDs de produtos separados por vírgula (todos os planos aceitos)
    CAKTO_SUBSCRIPTION_PRODUCT_IDS: Optional[str] = "8e9qxyg,8e9qxyg_742442,hi5cerw,6bpwn57"
    CAKTO_ENFORCE_SUBSCRIPTION: bool = False
    CAKTO_WEBHOOK_SECRET: Optional[str] = "476ebb07-50c3-47ab-8bc6-f0e39f9e965d"
    
    # Planos Cakto disponíveis
    CAKTO_PLANS: Dict[str, Dict[str, str]] = {
        "principal": {
            "id": "8e9qxyg",
            "name": "Oferta Principal",
            "checkout_url": "https://pay.cakto.com.br/8e9qxyg_742442",
            "period": "mensal"
        },
        "trimestral": {
            "id": "3frhhks",
            "name": "MarketDash Trimestral",
            "checkout_url": "https://pay.cakto.com.br/3frhhks",
            "period": "trimestral"
        },
        "anual": {
            "id": "ebrg3ir",
            "name": "MarketDash Anual",
            "checkout_url": "https://pay.cakto.com.br/ebrg3ir",
            "period": "anual"
        }
    }
    
    def get_cakto_plan(self, plan_id: str) -> Optional[Dict[str, str]]:
        """Retorna informações de um plano específico ou None se não existir."""
        return self.CAKTO_PLANS.get(plan_id)
    
    def get_all_cakto_plans(self) -> Dict[str, Dict[str, str]]:
        """Retorna todos os planos disponíveis."""
        return self.CAKTO_PLANS.copy()
    
    # Email / SMTP Configuration
    SMTP_HOST: str = "smtp.hostinger.com"
    SMTP_PORT: int = 465
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None
    SMTP_FROM_NAME: str = "MarketDash"
    FRONTEND_URL: str = "https://marketdash.com.br"
    
    # CORS Configuration
    # Por padrão, apenas HTTPS é permitido em produção/homologação
    # HTTP é permitido apenas para desenvolvimento local
    # Para emergências, use FORCE_HTTP_FALLBACK=true (não recomendado)
    FORCE_HTTP_FALLBACK: bool = False
    
    CORS_ORIGINS: list[str] = [
        # Development (HTTP permitido apenas em localhost)
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:8080",
        # Production (HTTPS)
        "https://marketdash.com.br",
        "https://api.marketdash.com.br",
        "http://marketdash.com.br",
        "http://api.marketdash.com.br",
        # Homologation (HTTPS + HTTP para redirect/SSL temporário)
        "https://hml.marketdash.com.br",
        "https://api.hml.marketdash.com.br",
        "http://hml.marketdash.com.br",
        "http://api.hml.marketdash.com.br",
        # Alternativas de domínio de homologação
        "https://marketdash.hml.com.br",
        "https://api.marketdash.hml.com.br",
        "http://marketdash.hml.com.br",
        "http://api.marketdash.hml.com.br",
    ]
    
    def get_cors_origins(self) -> list[str]:
        """
        Retorna lista de origens CORS permitidas.
        
        Se FORCE_HTTP_FALLBACK estiver ativo, adiciona URLs HTTP de produção/homologação.
        ATENÇÃO: Use apenas em emergências críticas. Deve ser removido assim que SSL for corrigido.
        """
        origins = self.CORS_ORIGINS.copy()
        
        if self.FORCE_HTTP_FALLBACK:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                "⚠️ FORCE_HTTP_FALLBACK está ativo! "
                "Isso é temporário e deve ser removido assim que SSL for corrigido."
            )
            # Adicionar URLs HTTP de produção/homologação temporariamente
            origins.extend([
                "http://marketdash.com.br",
                "http://api.marketdash.com.br",
                "http://hml.marketdash.com.br",
                "http://api.hml.marketdash.com.br",
                "http://marketdash.hml.com.br",
                "http://api.marketdash.hml.com.br",
            ])
        
        return origins
    
    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()

