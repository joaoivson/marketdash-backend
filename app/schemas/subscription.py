from pydantic import BaseModel
from typing import List, Optional


class PlanInfo(BaseModel):
    """Informações de um plano de assinatura."""
    id: str
    name: str
    checkout_url: str
    period: str  # "mensal", "trimestral", "anual"
    
    class Config:
        from_attributes = True


class PlansResponse(BaseModel):
    """Resposta com lista de planos disponíveis."""
    plans: List[PlanInfo]


class CancelSubscriptionResponse(BaseModel):
    """Resposta ao cancelar assinatura."""
    message: str
    subscription_cancelled: bool
    note: Optional[str] = None
