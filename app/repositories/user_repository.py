from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_email(self, email: str) -> Optional[User]:
        if not email:
            return None
        normalized = email.strip().lower()
        return (
            self.db.query(User)
            .filter(func.lower(User.email) == normalized)
            .first()
        )

    def get_by_cpf(self, cpf_cnpj: str) -> Optional[User]:
        if not cpf_cnpj:
            return None
        normalized = cpf_cnpj.strip()
        return self.db.query(User).filter(User.cpf_cnpj == normalized).first()

    def get_by_id(self, user_id: int) -> Optional[User]:
        return self.db.query(User).filter(User.id == user_id).first()
    
    def get_by_password_set_token(self, token: str) -> Optional[User]:
        return self.db.query(User).filter(User.password_set_token == token).first()

    def get_first(self) -> Optional[User]:
        return self.db.query(User).first()

    def create(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
    
    def update(self, user: User) -> User:
        self.db.commit()
        self.db.refresh(user)
        return user
