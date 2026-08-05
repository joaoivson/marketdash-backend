"""Acesso ao extrato de créditos de IA."""
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.ai_credit_ledger import AiCreditLedger


class AiCreditRepository:
    def __init__(self, db: Session):
        self.db = db

    def total_gasto_no_mes(self, user_id: int, inicio_do_mes: datetime) -> int:
        total = (
            self.db.query(func.coalesce(func.sum(AiCreditLedger.creditos), 0))
            .filter(
                AiCreditLedger.user_id == user_id,
                AiCreditLedger.criado_em >= inicio_do_mes,
            )
            .scalar()
        )
        return int(total or 0)

    def registrar(self, user_id, diagnostic_id, tipo, creditos, saldo_apos) -> AiCreditLedger:
        linha = AiCreditLedger(
            user_id=user_id,
            diagnostic_id=diagnostic_id,
            tipo=tipo,
            creditos=creditos,
            saldo_apos=saldo_apos,
        )
        self.db.add(linha)
        self.db.commit()
        return linha
