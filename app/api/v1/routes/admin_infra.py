"""
Painel de infraestrutura — SÓ admin, SÓ leitura.

Uma rota, um GET, tudo de uma vez: quem abre este painel está perguntando "o
que está no ar agora?", e a resposta dividida em cinco requests deixaria a
tela montar meia verdade primeiro (que é como se olha um número e se decide
errado).

**Não existe POST/PATCH/DELETE aqui, e é de propósito.** Restart e deploy
continuam no Coolify, com a autenticação do Coolify. Ver o cabeçalho de
`app/services/infra_status_service.py`.
"""
import logging

from fastapi import APIRouter, Depends, Query

from app.api.v1.dependencies import require_admin
from app.models.user import User
from app.schemas.infra import InfraOut
from app.services import infra_status_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin-infra"])


@router.get("/infra", response_model=InfraOut)
async def status_da_infra(
    _: User = Depends(require_admin),
    forcar: bool = Query(
        False,
        description="Ignora o cache de 30s. É o que o botão 'Atualizar' manda — "
        "gesto humano deliberado, não recarga automática.",
    ),
) -> dict:
    """Coolify, containers, VPS, pontas públicas e filas do Celery.

    Cada bloco carrega o próprio `erro`: Coolify fora do ar não esconde o
    resultado das pontas HTTP, que é justamente o bloco que importa quando
    algo está quebrado.
    """
    return await infra_status_service.coletar(forcar=forcar)
