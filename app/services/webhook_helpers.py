"""
Lógica compartilhada entre webhooks de Cakto e Kiwify.

Funções extraídas do cakto.py para reutilização.
"""

import logging
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService

logger = logging.getLogger(__name__)


def find_or_create_user(
    email: str,
    customer_data: Dict[str, Any],
    db: Session,
) -> Tuple[User, bool, bool]:
    """
    Busca ou cria usuário a partir dos dados do webhook.

    Returns:
        (user, user_created, user_has_password)
    """
    user_repo = UserRepository(db)
    user = user_repo.get_by_email(email)

    if not user:
        doc_id = customer_data.get("cpf_cnpj")
        if doc_id:
            user = user_repo.get_by_cpf(doc_id)
            if user:
                logger.info(f"Usuário ID {user.id} encontrado por documento. Atualizando email para {email}")
                user.email = email
                db.commit()
                db.refresh(user)

    if not user:
        logger.info(f"Criando novo usuário via webhook: {email}")
        auth_service = AuthService(user_repo)
        user = auth_service.register_from_webhook(
            email=customer_data.get("email") or email,
            name=customer_data.get("name"),
            cpf_cnpj=customer_data.get("cpf_cnpj"),
        )
        return user, True, False

    user_has_password = not bool(user.password_set_token)
    logger.info(f"Usuário {user.id} já existe. Tem senha: {user_has_password}")
    return user, False, user_has_password


def send_subscription_email(
    user: User,
    email: str,
    user_created: bool,
    user_has_password: bool,
    customer_name: Optional[str],
    db: Session,
) -> None:
    """Envia email de ativação/reativação. Falhas são logadas sem re-raise."""
    try:
        from app.services.email_service import EmailService

        email_service = EmailService()
        user_name = customer_name or user.name or "Usuário"

        if user_created:
            logger.info(f"Usuário novo: email de boas-vindas já enviado via register_from_webhook")
        elif user_has_password:
            logger.info(f"Enviando e-mail de reativação para {email}")
            email_service.send_welcome_back_email(user_email=email, user_name=user_name)
        else:
            logger.info(f"Usuário sem senha: Enviando link de definição para {email}")
            token = user.password_set_token
            if not token:
                token = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
                user.password_set_token = token
                user.password_set_token_expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
                db.commit()
            email_service.send_set_password_email(user_email=email, user_name=user_name, token=token)
    except Exception as e:
        logger.error(f"FALHA NO ENVIO DE E-MAIL: {str(e)}")


def calculate_expires_at(
    due_date: Optional[datetime],
    recurrence_period: Optional[int],
    paid_at: Optional[datetime],
) -> Optional[datetime]:
    """Calcula a data de expiração da assinatura."""
    now_utc = datetime.now(timezone.utc)
    period_days = int(recurrence_period) if recurrence_period else 30

    if due_date:
        expires = due_date
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires < now_utc:
            while expires < now_utc:
                expires = expires + timedelta(days=period_days)
            logger.info(f"due_date estava no passado. Nova expires_at: {expires.isoformat()}")
        return expires

    base = paid_at if paid_at else now_utc
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    expires = base + timedelta(days=period_days)
    logger.info(f"Sem due_date. expires_at calculada: {expires.isoformat()}")
    return expires
