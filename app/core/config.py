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

    # WhatsApp via WAHA auto-hospedado (waha.devlike.pro, engine GOWS) —
    # substituiu a Evolution API em 25/08 (RAM ~60MB/sessão vs 300-500MB, e a
    # Evolution 2.4 passou a exigir ativação com telemetria). Sem as duas
    # primeiras, toda feature de WhatsApp fica indisponível em vez de quebrar.
    WAHA_URL: Optional[str] = None
    WAHA_API_KEY: Optional[str] = None
    # Sessão global do RESUMO DIÁRIO (o número do MarketDash). As sessões das
    # afiliadas são por-usuária e vivem em whatsapp_instancias.
    WAHA_SESSAO_RESUMO: Optional[str] = None
    # Autentica o caminho de volta (webhook). Vai como customHeader
    # X-Webhook-Token e como chave HMAC nas sessões.
    WAHA_WEBHOOK_TOKEN: Optional[str] = None
    # URL PÚBLICA do webhook (ex.: https://api.hml.marketdash.com.br/api/v1/whatsapp/webhook).
    # Nunca derivar de url_for: atrás do proxy sai http:// e falha em silêncio.
    WAHA_WEBHOOK_URL: Optional[str] = None
    # Travas anti-banimento do resumo diário: intervalo entre mensagens e teto.
    WHATSAPP_INTERVALO_MIN_S: float = 3.0
    WHATSAPP_INTERVALO_MAX_S: float = 8.0
    WHATSAPP_TETO_DIARIO: int = 300
    WHATSAPP_FALHAS_PARA_PARAR: int = 5
    # Cap global de sessões de afiliadas — proteção de RAM do servidor WAHA.
    WHATSAPP_MAX_INSTANCIAS_GLOBAL: int = 60
    # --- Motor de envio em grupos (F3). Ritmo é config de SISTEMA, nunca UI:
    # a afiliada não tem como saber o que é seguro, e o número dela é o ativo.
    WHATSAPP_GRUPO_PAUSA_MIN_S: float = 8.0     # pausa entre RODADAS
    WHATSAPP_GRUPO_PAUSA_MAX_S: float = 20.0
    WHATSAPP_GRUPO_JITTER_MIN_S: float = 1.0    # respiro dentro da rodada
    WHATSAPP_GRUPO_JITTER_MAX_S: float = 3.0
    WHATSAPP_RODADA_TAMANHO: int = 2            # msgs por rodada (padrão de mercado)
    WHATSAPP_TETO_POR_INSTANCIA: int = 80       # msgs/dia por número (3×80=240=teto MAX)
    WHATSAPP_CAMPANHA_TETO_GLOBAL_DIA: int = 5000  # proteção da plataforma
    WHATSAPP_FATIA_ORCAMENTO_S: int = 900       # ~15min por fatia (< task_time_limit 1200)
    WHATSAPP_HASH_SALT: Optional[str] = None    # sha256(jid+salt) p/ eventos (F6)

    # --- IA (F4): SÓ gera variações de template na tela de Templates. Nunca no
    # caminho do envio — variação sorteada tem custo zero, latência zero e não
    # inventa preço. Chave NOVA: a antiga vazou em 22/08 e foi revogada.
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODELO: str = "gpt-4o-mini"

    # Processar CSV na própria requisição (síncrono), sem Celery. Use quando não houver worker (ex.: Coolify sem worker).
    # Os dados ficam disponíveis logo após o upload. Para arquivos muito grandes prefira Celery + worker.
    PROCESS_CSV_SYNC: bool = False

    # Abaixo deste tamanho o CSV é processado na própria requisição, sem fila.
    # Um relatório de cliques típico tem ~20 KB e leva ~2 ms pra parsear — mandar
    # isso pro Celery só adiciona um modo de falha: se a task não for consumida,
    # o dataset fica "pending" pra sempre, sem erro, e a tela do usuário gira
    # indefinidamente. Arquivos grandes continuam indo pra fila.
    CSV_SYNC_MAX_BYTES: int = 2 * 1024 * 1024

    # Jobs pipeline: upload via presigned URL + chunking (Object Storage + Celery). Se False, rotas /jobs não são registradas.
    USE_JOBS_PIPELINE: bool = False

    # Object Storage (S3-compatible, ex.: Supabase Storage). Se ausentes, pipeline jobs desativada ou fallback.
    S3_BUCKET: Optional[str] = None
    S3_ENDPOINT: Optional[str] = None
    S3_ACCESS_KEY: Optional[str] = None
    S3_SECRET_KEY: Optional[str] = None
    S3_REGION: Optional[str] = None

    # Shopee Affiliate API — chave Fernet (base64) para criptografar senhas armazenadas.
    # A MESMA chave é reutilizada para criptografar os access tokens do Facebook.
    SHOPEE_ENCRYPTION_KEY: Optional[str] = None

    # Facebook Marketing API (campanhas). Criar app em developers.facebook.com.
    # Permissões necessárias: ads_read (leitura) + ads_management (pausar/ativar/orçamento).
    FACEBOOK_APP_ID: Optional[str] = None
    FACEBOOK_APP_SECRET: Optional[str] = None
    FACEBOOK_API_VERSION: str = "v25.0"
    # Redirect URI registrada no app do Facebook (deve bater exatamente com a do frontend).
    # Ex.: https://app.marketdash.com.br/dashboard/configuracoes
    FACEBOOK_OAUTH_REDIRECT_URI: Optional[str] = None
    # Login do Facebook para Empresas: ID da configuração OAuth (substitui `scope` no diálogo).
    # Painel → Login do Facebook para Empresas → Configurações → criar config (User access token).
    FACEBOOK_OAUTH_CONFIG_ID: Optional[str] = None

    # ---------------------------------------------------------------- #
    #  Instagram — Business Login for Instagram (automação comentário→DM) #
    # ---------------------------------------------------------------- #
    # ATENÇÃO: NÃO são o FACEBOOK_APP_ID/SECRET. O caso de uso "Instagram →
    # API setup with Instagram login" gera credenciais PRÓPRIAS dentro do mesmo
    # app da Meta. Usar o App ID do Facebook aqui faz o OAuth falhar com
    # "Invalid platform app".
    #
    # Escolha deliberada (ver docs/AUTOMACAO_INSTAGRAM.md §3.1): este caminho não
    # exige Página do Facebook vinculada e é isolado da configuração de anúncios
    # — se uma permissão do Instagram for revogada, o Meta Ads não cai junto.
    INSTAGRAM_APP_ID: Optional[str] = None
    INSTAGRAM_APP_SECRET: Optional[str] = None
    # Deve bater EXATAMENTE com a Redirect URL registrada no painel da Meta.
    INSTAGRAM_OAUTH_REDIRECT_URI: Optional[str] = None
    # Token do handshake GET /webhooks/instagram (hub.verify_token). Gerar com
    # openssl rand -hex 32 e cadastrar igual no painel de Webhooks da Meta.
    INSTAGRAM_WEBHOOK_VERIFY_TOKEN: Optional[str] = None
    # Versão da Instagram Graph API (host graph.instagram.com).
    INSTAGRAM_API_VERSION: str = "v25.0"

    # Travas anti-bloqueio do envio. O teto da Meta é 750 private replies/hora
    # por conta profissional; 600 deixa margem para o que já foi gasto fora do
    # MarketDash. 5/s é limite auto-imposto — a API aceita mais, mas rajada é o
    # que faz o Instagram tratar a conta como bot.
    INSTAGRAM_MAX_PRIVATE_REPLIES_HORA: int = 600
    INSTAGRAM_MAX_ENVIOS_SEGUNDO: int = 5

    # pg_cron (Supabase) → endpoint interno do backend. Secret compartilhado (X-Cron-Secret).
    # Gerar com: openssl rand -hex 32. Quando None, o endpoint /internal/cron/* retorna 503.
    CRON_SECRET: Optional[str] = None

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

    # Subscription enforcement (generic, replaces CAKTO_ENFORCE_SUBSCRIPTION)
    ENFORCE_SUBSCRIPTION: bool = False

    # Kiwify Integration
    KIWIFY_API_BASE: str = "https://public-api.kiwify.com/v1"
    KIWIFY_ACCOUNT_ID: Optional[str] = None
    KIWIFY_CLIENT_SECRET: Optional[str] = None
    KIWIFY_WEBHOOK_SECRET: Optional[str] = None
    KIWIFY_SUBSCRIPTION_PRODUCT_IDS: Optional[str] = None

    KIWIFY_PLANS: Dict[str, Dict[str, str]] = {
        "mensal": {
            "id": "mensal_kiwify",
            "name": "MarketDash Mensal",
            "checkout_url": "https://pay.kiwify.com.br/u12boOS",
            "period": "mensal",
        },
        "trimestral": {
            "id": "trimestral_kiwify",
            "name": "MarketDash Trimestral",
            "checkout_url": "https://pay.kiwify.com.br/9B9lXa6",
            "period": "trimestral",
        },
        "anual": {
            "id": "anual_kiwify",
            "name": "MarketDash Anual",
            "checkout_url": "https://pay.kiwify.com.br/4lhuudg",
            "period": "anual",
        },
    }

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
            "id": "u6rpnpo",
            "name": "MarketDash Trimestral",
            "checkout_url": "https://pay.cakto.com.br/u6rpnpo",
            "period": "trimestral"
        },
        "anual": {
            "id": "yda8io6",
            "name": "MarketDash Anual",
            "checkout_url": "https://pay.cakto.com.br/yda8io6",
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
        "http://127.0.0.1:8080",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
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

