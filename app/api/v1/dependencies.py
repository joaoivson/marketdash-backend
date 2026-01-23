from typing import Optional
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.repositories.user_repository import UserRepository

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
            # Tentar decodificar sem verificar para ver o erro
            unverified = jwt.decode(token, options={"verify_signature": False})
            logger.error(f"Token decodificado sem verificação: {unverified}")
        except Exception as e:
            logger.error(f"Erro ao decodificar token sem verificação: {e}")
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
    from app.repositories.dataset_row_repository import DatasetRowRepository
    from app.repositories.ad_spend_repository import AdSpendRepository
    row_repo = DatasetRowRepository(db)
    ad_spend_repo = AdSpendRepository(db)
    
    total_rows = row_repo.db.query(row_repo.model).filter(row_repo.model.user_id == user_id).count()
    total_ad_spends = ad_spend_repo.db.query(ad_spend_repo.model).filter(ad_spend_repo.model.user_id == user_id).count()
    
    logger.info(f"Estatísticas do usuário {user_id}: {total_rows} dataset_rows, {total_ad_spends} ad_spends")
    
    return user
