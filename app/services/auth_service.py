from datetime import timedelta

from fastapi import HTTPException, status

from app.core.config import settings
from app.core.security import verify_password, get_password_hash, create_access_token
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.cakto_service import check_active_subscription, CaktoError


class AuthService:
    def __init__(self, user_repo: UserRepository):
        self.user_repo = user_repo

    def register(self, user_data) -> User:
        existing = self.user_repo.get_by_email(user_data.email)
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email já cadastrado")

        hashed = get_password_hash(user_data.password)
        new_user = User(
            name=user_data.name,
            cpf_cnpj=user_data.cpf_cnpj,
            email=user_data.email,
            hashed_password=hashed,
            is_active=True,
        )
        return self.user_repo.create(new_user)

    def login(self, email: str, password: str):
        user = self.user_repo.get_by_email(email)
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Email ou senha incorretos",
                headers={"WWW-Authenticate": "Bearer"},
            )
        if not user.is_active:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Usuário inativo")

        if settings.CAKTO_ENFORCE_SUBSCRIPTION:
            try:
                has_access, reason = check_active_subscription(user.email)
                if not has_access:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail=reason or "Assinatura ativa não encontrada",
                    )
            except CaktoError as e:
                # Em ambiente de desenvolvimento/homologação, permitir login mesmo se Cakto falhar
                # Em produção, bloquear login se a validação falhar
                if settings.ENVIRONMENT in ("development", "staging", "homologation"):
                    # Log do erro mas permite login em ambientes de desenvolvimento/homologação
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.warning(f"Cakto validation failed for {user.email} in {settings.ENVIRONMENT}: {str(e)}")
                else:
                    # Em produção, bloquear login se Cakto falhar
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Falha ao validar assinatura. Tente novamente.",
                    )

        access_token_expires = timedelta(hours=settings.JWT_EXPIRATION_HOURS)
        # JWT requer que 'sub' seja uma string, não um número
        access_token = create_access_token(
            data={"sub": str(user.id)},
            expires_delta=access_token_expires,
        )
        return {"access_token": access_token, "token_type": "bearer", "user": user}
