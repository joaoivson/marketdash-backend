import logging
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


def _get_fernet():
    from app.core.config import settings
    key = settings.SHOPEE_ENCRYPTION_KEY
    if not key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SHOPEE_ENCRYPTION_KEY não configurada no servidor.",
        )
    from cryptography.fernet import Fernet
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt_value(plain: str) -> str:
    """Criptografa um valor usando Fernet. Lança HTTP 500 se a chave estiver ausente."""
    f = _get_fernet()
    return f.encrypt(plain.encode()).decode()


def decrypt_value(token: str) -> str:
    """Descriptografa um token Fernet."""
    f = _get_fernet()
    return f.decrypt(token.encode()).decode()
