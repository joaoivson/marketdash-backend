import os
from typing import Optional, Dict

from pydantic import model_validator
from pydantic_settings import BaseSettings


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

    # Upload de arquivos grandes (ex.: CSV 500k+ linhas)
    # Se definido, o arquivo é gravado em disco e apenas o caminho é enviado ao Celery (evita Redis com payload gigante).
    # API e worker precisam enxergar o mesmo diretório (ex.: volume compartilhado). Ex.: /app/uploads
    UPLOAD_TEMP_DIR: Optional[str] = None

    # Se UPLOAD_TEMP_DIR estiver definido e o arquivo for menor que este tamanho (bytes), o conteúdo é enviado
    # em base64 na tarefa Celery (evita "Upload temp file not found" quando API e worker não compartilham disco).
    # Arquivos maiores que este limite exigem volume compartilhado entre API e worker. Default: 5 MB.
    UPLOAD_INLINE_MAX_BYTES: int = 5 * 1024 * 1024

    # Processar CSV na própria requisição (síncrono), sem Celery. Use quando não houver worker (ex.: Coolify sem worker).
    # Os dados ficam disponíveis logo após o upload. Para arquivos muito grandes prefira Celery + worker.
    PROCESS_CSV_SYNC: bool = False

    # Jobs pipeline: upload via presigned URL + chunking (Object Storage + Celery). Se False, rotas /jobs não são registradas.
    USE_JOBS_PIPELINE: bool = False

    # Object Storage (S3-compatible, ex.: Supabase Storage). Se ausentes, pipeline jobs desativada ou fallback.
    S3_BUCKET: Optional[str] = None
    S3_ENDPOINT: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_REGION: Optional[str] = None

    # Debug: caminho do arquivo de log NDJSON (agent debug). Em Docker use ex.: /app/.cursor/debug.log
    DEBUG_LOG_PATH: Optional[str] = None

    @property
    def effective_debug_log_path(self) -> str:
        """Path do arquivo de log de debug (env/config ou default em cwd)."""
        return self.DEBUG_LOG_PATH or os.environ.get("DEBUG_LOG_PATH") or os.path.join(os.getcwd(), ".cursor", "debug.log")

    @model_validator(mode='after')
    def assemble_redis_url(self) -> 'Settings':
        if self.REDIS_PASSWORD and self.REDIS_URL:
            # Se a URL já contiver senha (ex: :password@...), não fazemos nada
            if "@" in self.REDIS_URL:
                return self
                
            import urllib.parse
            # Se a URL não tem senha mas temos REDIS_PASSWORD, injetamos
            if "redis://" in self.REDIS_URL:
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
    # Email que recebe feedback do formulário (API de feedback)
    FEEDBACK_EMAIL: str = "relacionamento@marketdash.com.br"
    
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

