from typing import Optional
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.core.config import settings
from app.db.session import get_db
from app.repositories.user_repository import UserRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService
from app.models.user import User

logger = logging.getLogger(__name__)
security = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: Session = Depends(get_db),
):
    if credentials is None:
        logger.warning("Token de autenticação não fornecido - credentials é None")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = credentials.credentials
    if not token or not token.strip():
        logger.warning(f"Token vazio ou inválido - token: '{token}', type: {type(token)}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Log detalhado para debug (apenas primeiros e últimos caracteres por segurança)
    token_preview = f"{token[:10]}...{token[-10:]}" if len(token) > 20 else token
    logger.info(f"Validando token: {token_preview} (length: {len(token)})")
    
    # Verificar se o token começa com "Bearer " e remover se necessário
    if token.startswith("Bearer "):
        logger.warning("Token contém prefixo 'Bearer ' - removendo")
        token = token[7:].strip()
    
    payload = decode_access_token(token)
    if payload is None:
        logger.error(f"Falha ao decodificar token (token length: {len(token)}, preview: {token_preview})")
        # Tentar decodificar sem verificação para ver o erro específico
        try:
            from jose import jwt
            from app.core.config import settings
            # Tentar decodificar sem verificar para ver o erro (precisa passar key mesmo sem verificar)
            unverified = jwt.decode(token, key="", options={"verify_signature": False, "verify_sub": False})
            logger.error(f"Token decodificado sem verificação: {unverified}")
            logger.error(f"Token expirado? exp={unverified.get('exp')}, agora={__import__('time').time()}")
            logger.error(f"Sub no token: {unverified.get('sub')} (tipo: {type(unverified.get('sub'))})")
        except Exception as e:
            logger.error(f"Erro ao decodificar token sem verificação: {e}")
            import traceback
            logger.error(f"Traceback: {traceback.format_exc()}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"Token decodificado com sucesso. Payload: {list(payload.keys())}")
    
    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        logger.error(f"Token não contém 'sub' (user_id). Payload keys: {list(payload.keys())}, payload: {payload}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_id = int(user_id_raw)
        logger.info(f"User ID extraído do token: {user_id}")
    except (ValueError, TypeError) as e:
        logger.error(f"Erro ao converter user_id do token: {user_id_raw} (tipo: {type(user_id_raw)}). Erro: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = UserRepository(db).get_by_id(user_id)
    if user is None:
        logger.error(f"Usuário com ID {user_id} não encontrado no banco de dados")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuário não encontrado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        logger.warning(f"Usuário {user_id} está inativo")
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuário inativo")
    
    logger.info(f"Autenticação bem-sucedida para usuário {user_id} ({user.email})")
    
    # Log adicional para verificar se há dados no banco para este usuário
    from app.models.dataset_row import DatasetRow
    from app.models.ad_spend import AdSpend
    
    total_rows = db.query(DatasetRow).filter(DatasetRow.user_id == user_id).count()
    total_ad_spends = db.query(AdSpend).filter(AdSpend.user_id == user_id).count()
    
    logger.info(f"Estatísticas do usuário {user_id}: {total_rows} dataset_rows, {total_ad_spends} ad_spends")
    
    # Verificar todos os user_ids únicos no banco (para debug)
    all_row_user_ids = [uid[0] for uid in db.query(DatasetRow.user_id).distinct().all()]
    all_ad_spend_user_ids = [uid[0] for uid in db.query(AdSpend.user_id).distinct().all()]
    logger.info(f"User IDs únicos no banco - dataset_rows: {all_row_user_ids}, ad_spends: {all_ad_spend_user_ids}")
    
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
