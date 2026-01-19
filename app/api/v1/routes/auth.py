from fastapi import APIRouter, Depends, Form, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.schemas.user import UserCreate, UserResponse, UserUpdate, TokenWithUser
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


@router.get("/me", response_model=UserResponse)
def get_me(current_user=Depends(get_current_user)):
    return current_user


@router.put("/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: int,
    user_update: UserUpdate,
    db: Session = Depends(get_db),
):
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
def delete_user(user_id: int, db: Session = Depends(get_db)):
    user_repo = UserRepository(db)
    user = user_repo.get_by_id(user_id)
    if not user:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    db.delete(user)
    db.commit()
    return
