from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    """Corpo da requisição de feedback (formulário)."""

    data: Dict[str, Any] = Field(
        ...,
        description="Objeto com os campos coletados no formulário de feedback (ex.: message, rating, category, etc.)",
    )
    name: Optional[str] = Field(None, description="Nome do usuário (opcional)")
    email: Optional[str] = Field(None, description="Email do usuário (opcional)")


class FeedbackResponse(BaseModel):
    """Resposta do envio de feedback."""

    success: bool = Field(..., description="Se o email foi enviado com sucesso")
    message: str = Field(..., description="Mensagem de retorno")
