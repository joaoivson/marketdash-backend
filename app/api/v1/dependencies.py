from typing import Optional
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import text
from supabase import create_client, Client

from app.core.config import settings
from app.db.session import get_db
from app.repositories.user_repository import UserRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService
from app.models.user import User

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)

# Initialize Supabase client lazily to avoid crash during import if credentials are missing
_supabase: Optional[Client] = None


def get_supabase_client() -> Client:
    """Gets or initializes the Supabase client."""
    global _supabase
    if _supabase is None:
        if not settings.SUPABASE_URL or not settings.SUPABASE_KEY:
            logger.error("SUPABASE_URL ou SUPABASE_KEY não configurados")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Configuração do Supabase ausente no servidor"
            )
        try:
            _supabase = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        except Exception as e:
            logger.error(f"Falha ao inicializar cliente Supabase: {e}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Erro ao conectar com serviço de autenticação"
            )
    return _supabase


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        logger.warning("Token de autenticação não fornecido")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    
    # Validar token com Supabase Auth
    try:
        supabase = get_supabase_client()
        user_supabase = supabase.auth.get_user(token)
        if not user_supabase or not user_supabase.user:
            raise HTTPException(status_code=401, detail="Token inválido ou expirado")
        
        email = user_supabase.user.email
    except Exception as e:
        logger.error(f"Erro ao validar token no Supabase: {e}")
        raise HTTPException(status_code=401, detail="Token inválido ou expirado")

    # Buscar usuário no banco local pelo email (Link por Email)
    user = UserRepository(db).get_by_email(email)
    
    if user is None:
        logger.error(f"Usuário com email {email} não encontrado no banco local")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não sincronizado com o dashboard",
        )
    
    if not user.is_active:
        logger.warning(f"Usuário {user.id} está inativo")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuário inativo")

    # --- CRÍTICO PARA SEGURANÇA ---
    # Injetar o ID do usuário na sessão do PostgreSQL para ativar o RLS
    try:
        db.execute(text(f"SET LOCAL app.current_user_id = '{user.id}';"))
    except Exception as e:
        logger.error(f"Falha ao configurar contexto RLS para o usuário {user.id}: {e}")
        # Se falhar a configuração do RLS, bloqueamos o request por segurança
        raise HTTPException(status_code=500, detail="Erro interno de segurança")

    logger.info(f"Autenticação Supabase OK: {user.email} (ID: {user.id})")
    
    return user


def require_active_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> User:
    """
    Dependency que verifica se o usuário tem assinatura ativa.
    Se passou mais de 30 dias desde a última validação, valida com Cakto.
    Retorna 403 se assinatura não estiver ativa.
    """
    if not settings.CAKTO_ENFORCE_SUBSCRIPTION:
        # Se não está habilitado, permite acesso
        return current_user
    
    subscription_service = SubscriptionService(SubscriptionRepository(db))
    
    # Verificar se precisa validar (passou mais de 30 dias)
    needs_validation = subscription_service.needs_validation(current_user.id)
    
    if needs_validation:
        # Validar com Cakto
        logger.info(f"Validando assinatura do usuário {current_user.id} (passou mais de 30 dias)")
        is_active = subscription_service.check_and_update_subscription(
            current_user.id, 
            current_user.email
        )
        
        if not is_active:
            logger.warning(f"Usuário {current_user.id} não tem assinatura ativa")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Assinatura não está ativa. Por favor, renove sua assinatura.",
            )
    else:
        # Usar cache (verificar is_active no banco)
        subscription = subscription_service.repo.get_by_user_id(current_user.id)
        if not subscription or not subscription.is_active:
            logger.warning(f"Usuário {current_user.id} não tem assinatura ativa (cache)")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Assinatura não está ativa. Por favor, renove sua assinatura.",
            )
    
    return current_user
