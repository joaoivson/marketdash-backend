"""
API de feedback: recebe mensagem e dados do usuário e envia email para relacionamento@marketdash.com.br.
Substitui o uso de FormSubmit.co para garantir que o feedback chegue ao email configurado.
"""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, status

from app.api.v1.dependencies import get_current_user_optional
from app.models.user import User
from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services.email_service import EmailService

logger = logging.getLogger(__name__)

router = APIRouter(tags=["feedback"])


@router.post(
    "/",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
    summary="Enviar feedback",
    description="Envia o feedback e dados do usuário por email para relacionamento@marketdash.com.br. "
    "Autenticação opcional: se o usuário estiver logado, o email inclui também id/email/nome do usuário.",
)
def send_feedback(
    body: FeedbackRequest,
    current_user: Optional[User] = Depends(get_current_user_optional),
) -> FeedbackResponse:
    """
    Recebe a mensagem de feedback (e opcionalmente nome/email no body).
    Se o usuário estiver logado (Bearer token), enriquece o email com user_id, email e nome do usuário.
    """
    user_id: Optional[int] = None
    user_email: Optional[str] = body.email
    user_name: Optional[str] = body.name
    if current_user:
        user_id = current_user.id
        user_email = user_email or current_user.email
        user_name = user_name or current_user.name or current_user.email

    email_service = EmailService()
    success = email_service.send_feedback_email(
        data=body.data,
        user_name=user_name,
        user_email=user_email,
        user_id=user_id,
    )
    if success:
        return FeedbackResponse(success=True, message="Feedback enviado com sucesso.")
    return FeedbackResponse(
        success=False,
        message="Não foi possível enviar o feedback no momento. Tente novamente mais tarde.",
    )
