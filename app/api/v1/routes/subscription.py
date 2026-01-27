from fastapi import APIRouter, Depends, status, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone, timedelta
from typing import Optional

from app.api.v1.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.repositories.subscription_repository import SubscriptionRepository
from app.services.subscription_service import SubscriptionService
from app.schemas.subscription import CancelSubscriptionResponse

router = APIRouter(tags=["subscription"])


@router.get("/status")
def get_subscription_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retorna o status da assinatura do usuário atual."""
    subscription_service = SubscriptionService(SubscriptionRepository(db))
    status_data = subscription_service.get_subscription_status(current_user.id)
    return status_data


@router.get("/check-status")
def check_subscription_status(
    email: str = Query(..., description="Email do usuário para verificar"),
    db: Session = Depends(get_db),
):
    """
    Verifica se a assinatura foi ativada recentemente (últimos 5 minutos).
    Usado pelo frontend após retorno do checkout da Cakto para mostrar mensagem de sucesso.
    
    **Nota**: Este endpoint é público (não requer autenticação) para permitir verificação
    após o checkout da Cakto, antes do usuário fazer login.
    """
    from app.models.user import User
    from app.repositories.subscription_repository import SubscriptionRepository
    
    # Buscar usuário por email
    user = db.query(User).filter(User.email.ilike(email)).first()
    if not user:
        return {
            "subscription_activated": False,
            "message": "Usuário não encontrado"
        }
    
    # Buscar subscription
    subscription_repo = SubscriptionRepository(db)
    subscription = subscription_repo.get_by_user_id(user.id)
    
    if not subscription:
        return {
            "subscription_activated": False,
            "message": "Assinatura não encontrada"
        }
    
    # Verificar se foi ativada recentemente (últimos 5 minutos)
    now = datetime.now(timezone.utc)
    recently_activated = False
    
    if subscription.is_active and subscription.last_validation_at:
        time_diff = now - subscription.last_validation_at
        # Considerar ativada se foi validada nos últimos 5 minutos
        recently_activated = time_diff.total_seconds() <= 300  # 5 minutos
    
    return {
        "subscription_activated": recently_activated,
        "is_active": subscription.is_active,
        "last_validation_at": subscription.last_validation_at.isoformat() if subscription.last_validation_at else None,
        "message": "Assinatura ativada recentemente" if recently_activated else "Assinatura não foi ativada recentemente"
    }


@router.post("/cancel", response_model=CancelSubscriptionResponse, status_code=status.HTTP_200_OK)
def cancel_subscription(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Cancela a assinatura do usuário atual.
    
    **Importante:**
    - Este endpoint desativa a assinatura no nosso sistema
    - Para cancelar completamente na Cakto, o usuário deve fazer isso diretamente na plataforma Cakto
    - Quando a Cakto processar o cancelamento, o webhook atualizará automaticamente o status
    
    **Nota:** Se o usuário cancelar na Cakto, o webhook receberá o evento e atualizará
    automaticamente. Este endpoint é útil para desativar a assinatura imediatamente
    no nosso sistema enquanto o cancelamento na Cakto é processado.
    """
    subscription_service = SubscriptionService(SubscriptionRepository(db))
    subscription_repo = SubscriptionRepository(db)
    
    cancelled = subscription_service.cancel_subscription(current_user.id)
    
    if not cancelled:
        # Verificar se já estava cancelada ou não existe
        subscription = subscription_repo.get_by_user_id(current_user.id)
        if not subscription or not subscription.is_active:
            return CancelSubscriptionResponse(
                message="Assinatura já está cancelada ou não existe",
                subscription_cancelled=False,
                note="Sua assinatura já estava inativa."
            )
    
    return CancelSubscriptionResponse(
        message="Assinatura cancelada com sucesso",
        subscription_cancelled=True,
        note="Para cancelar completamente na Cakto, acesse sua conta na plataforma Cakto. O cancelamento será processado automaticamente via webhook."
    )
