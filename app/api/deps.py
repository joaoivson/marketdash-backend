from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.models.user import User
from app.core.security import decode_access_token

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db)
) -> User:
    """Get current authenticated user from JWT token."""
    token = credentials.credentials
    payload = decode_access_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido ou expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id_raw = payload.get("sub")
    if user_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    try:
        user_id = int(user_id_raw)
        user = db.query(User).filter(User.id == user_id).first()
    except (ValueError, TypeError):
        # Supabase ID (UUID) or other format
        # Fallback: Look up by email
        email = payload.get("email")
        if email:
            user = db.query(User).filter(User.email == email).first()
        else:
            # Token valid but no email to correlate
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token inválido: email não encontrado",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    if user is None:
        # Just-in-Time Provisioning for Supabase Users
        # Se o token é válido (assinado pelo Supabase) e tem email, mas usuário não existe localmente,
        # criamos o usuário localmente para permitir o funcionamento do backend.
        if email:
            # Importar aqui para evitar ciclo
            from app.core.security import get_password_hash
            from datetime import datetime, timezone
            import secrets
            import string
            
            # Gerar senha aleatória segura para conta gerenciada
            random_pwd = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
            
            new_user = User(
                email=email,
                hashed_password=get_password_hash(random_pwd),
                is_active=True,
                name=payload.get("user_metadata", {}).get("name") or payload.get("user_metadata", {}).get("full_name"),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc)
            )
            try:
                db.add(new_user)
                db.commit()
                db.refresh(new_user)
                user = new_user
            except Exception as e:
                db.rollback()
                # Se falhar a criação (ex: race condition), tentamos buscar novamente
                user = db.query(User).filter(User.email == email).first()
                if not user:
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Falha ao criar usuário local: {str(e)}"
                    )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Usuário não encontrado e token sem email",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuário inativo"
        )
    
    return user

