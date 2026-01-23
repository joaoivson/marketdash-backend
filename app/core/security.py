from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    """Hash a password (bcrypt limita a 72 bytes, truncamos para evitar erros)."""
    return pwd_context.hash(password[:72])


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=settings.JWT_EXPIRATION_HOURS)
    
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(
        to_encode, 
        settings.JWT_SECRET, 
        algorithm=settings.JWT_ALGORITHM
    )
    return encoded_jwt


def decode_access_token(token: str) -> Optional[dict]:
    """Decode and verify a JWT token."""
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        payload = jwt.decode(
            token, 
            settings.JWT_SECRET, 
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_signature": True, "verify_exp": True, "verify_sub": False}  # Desabilitar validação de sub como string
        )
        logger.info(f"Token decodificado com sucesso. Payload keys: {list(payload.keys())}")
        return payload
    except JWTError as e:
        logger.error(f"Erro JWT ao decodificar token: {e}")
        # Tentar decodificar sem verificação para ver o conteúdo
        try:
            unverified = jwt.decode(token, key="", options={"verify_signature": False, "verify_sub": False})
            logger.error(f"Token decodificado sem verificação: exp={unverified.get('exp')}, sub={unverified.get('sub')} (tipo: {type(unverified.get('sub'))})")
            import time
            if unverified.get('exp'):
                is_expired = unverified.get('exp') < time.time()
                logger.error(f"Token expirado? {is_expired} (exp={unverified.get('exp')}, agora={time.time()})")
        except Exception as decode_error:
            logger.error(f"Erro ao decodificar token sem verificação: {decode_error}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao decodificar token: {e}")
        import traceback
        logger.error(f"Traceback: {traceback.format_exc()}")
        return None

