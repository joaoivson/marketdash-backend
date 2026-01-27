from fastapi import APIRouter, Depends, Form, HTTPException, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.schemas.user import (
    UserCreate, 
    UserResponse, 
    UserUpdate, 
    TokenWithUser, 
    SetPasswordRequest, 
    SetPasswordResponse,
    ForgotPasswordRequest,
    ForgotPasswordResponse
)
from app.services.auth_service import AuthService
from app.repositories.user_repository import UserRepository

router = APIRouter(tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def register(user_data: UserCreate, db: Session = Depends(get_db)):
    return AuthService(UserRepository(db)).register(user_data)


@router.post("/login", response_model=TokenWithUser)
def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    return AuthService(UserRepository(db)).login(email, password)


@router.post("/forgot-password", response_model=ForgotPasswordResponse, status_code=status.HTTP_200_OK)
def forgot_password(
    request: ForgotPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Solicita reset de senha. Envia email com link para redefinir senha.
    
    Por segurança, sempre retorna sucesso mesmo se o email não existir,
    para prevenir enumeração de emails cadastrados.
    """
    auth_service = AuthService(UserRepository(db))
    auth_service.forgot_password(request.email)
    
    return ForgotPasswordResponse(
        message="Se o email estiver cadastrado, você receberá um link para redefinir sua senha."
    )


@router.post("/set-password", response_model=SetPasswordResponse, status_code=status.HTTP_200_OK)
def set_password(
    request: SetPasswordRequest,
    db: Session = Depends(get_db),
):
    """
    Define/redefine senha do usuário usando token recebido por email.
    
    Este endpoint é usado tanto para:
    - Novos usuários definirem senha pela primeira vez (após assinatura Cakto)
    - Usuários redefinirem senha esquecida (após solicitar forgot-password)
    """
    auth_service = AuthService(UserRepository(db))
    user = auth_service.set_password(request.token, request.password)
    
    return SetPasswordResponse(
        message="Senha definida com sucesso",
        user=UserResponse(
            id=user.id,
            name=user.name,
            cpf_cnpj=user.cpf_cnpj,
            email=user.email,
            is_active=user.is_active,
            created_at=user.created_at,
            updated_at=user.updated_at,
        )
    )


@router.get("/me", response_model=UserResponse)
def get_me(current_user=Depends(get_current_user)):
    return current_user


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_update: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Usuário só pode atualizar seu próprio perfil
    if current_user.id != user_id:
        from fastapi import HTTPException
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para atualizar este usuário"
        )
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(user_id)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    if user_update.name is not None:
        user.name = user_update.name
    if user_update.email is not None:
        existing = user_repo.get_by_email(user_update.email)
        if existing and existing.id != user_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="Email já cadastrado")
        user.email = user_update.email
    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Usuário só pode deletar seu próprio perfil
    if current_user.id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você não tem permissão para deletar este usuário"
        )
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado")
    db.delete(user)
    db.commit()
    return None
