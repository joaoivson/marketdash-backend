"""Acesso às sessões WAHA das afiliadas (whatsapp_instancias)."""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.whatsapp_grupos import (
    INSTANCIA_REMOVIDA, WhatsappInstancia,
)


class WhatsappInstanciaRepository:
    def __init__(self, db: Session):
        self.db = db

    def por_usuario(self, user_id: int, incluir_removidas: bool = False) -> List[WhatsappInstancia]:
        q = self.db.query(WhatsappInstancia).filter(WhatsappInstancia.user_id == user_id)
        if not incluir_removidas:
            q = q.filter(WhatsappInstancia.status != INSTANCIA_REMOVIDA)
        return q.order_by(WhatsappInstancia.id).all()

    def por_id(self, user_id: int, instancia_id: int) -> Optional[WhatsappInstancia]:
        return (
            self.db.query(WhatsappInstancia)
            .filter(
                WhatsappInstancia.id == instancia_id,
                WhatsappInstancia.user_id == user_id,
                WhatsappInstancia.status != INSTANCIA_REMOVIDA,
            )
            .first()
        )

    def por_nome(self, nome_instancia: str) -> Optional[WhatsappInstancia]:
        """Roteamento do webhook: o evento chega com o nome da sessão.

        Exclui removidas: o retry do WAHA entrega `session.status` DEPOIS do
        logout do remover — tratar ressuscitaria um número deletado (que
        voltaria a contar no limite do plano)."""
        return (
            self.db.query(WhatsappInstancia)
            .filter(
                WhatsappInstancia.nome_instancia == nome_instancia,
                WhatsappInstancia.status != INSTANCIA_REMOVIDA,
            )
            .first()
        )

    def total_global_ativas(self) -> int:
        """Cap global da plataforma — protege a RAM do servidor WAHA."""
        return (
            self.db.query(func.count(WhatsappInstancia.id))
            .filter(WhatsappInstancia.status != INSTANCIA_REMOVIDA)
            .scalar()
        ) or 0

    def salvar(self, instancia: WhatsappInstancia) -> WhatsappInstancia:
        instancia.atualizado_em = datetime.now(timezone.utc)
        self.db.add(instancia)
        self.db.commit()
        self.db.refresh(instancia)
        return instancia
