"""
Ponto de entrada do lote diário — o que o cron chama.

Existe separado do serviço porque roda fora de uma requisição: precisa abrir e
fechar a própria sessão de banco. Sem isso, a sessão da requisição já teria sido
devolvida ao pool quando o BackgroundTask começasse a rodar.
"""
import logging
from typing import Optional

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.user import User
from app.repositories.whatsapp_repository import WhatsappRepository
from app.services.waha_client import WahaClient
from app.services.whatsapp_envio_service import WhatsappEnvioService

logger = logging.getLogger(__name__)


def rodar_resumo_diario(apenas_user_id: Optional[int] = None) -> dict:
    db = SessionLocal()
    try:
        servico = WhatsappEnvioService(
            db=db,
            repo=WhatsappRepository(db),
            cliente=WahaClient(
                settings.WAHA_URL,
                settings.WAHA_API_KEY,
                settings.WAHA_SESSAO_RESUMO,
            ),
            buscar_usuario=lambda uid: db.query(User).filter(User.id == uid).first(),
        )
        return servico.enviar_lote(apenas_user_id=apenas_user_id).to_dict()
    except Exception:
        # O cron não lê exceção: se subir daqui, o lote some sem deixar rastro.
        logger.exception("Resumo diário no WhatsApp falhou por inteiro")
        return {"erro": "falha inesperada"}
    finally:
        db.close()
