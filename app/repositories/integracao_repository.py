"""Acesso às integrações de marketplace."""
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.integracao import Integracao


class IntegracaoRepository:
    def __init__(self, db: Session):
        self.db = db

    def por_usuario(self, user_id: int, provedor: Optional[str] = None,
                    apenas_ativas: bool = False) -> List[Integracao]:
        q = self.db.query(Integracao).filter(Integracao.user_id == user_id)
        if provedor:
            q = q.filter(Integracao.provedor == provedor)
        if apenas_ativas:
            q = q.filter(Integracao.ativa.is_(True))
        return q.order_by(Integracao.provedor, Integracao.label).all()

    def por_id(self, user_id: int, integracao_id: int) -> Optional[Integracao]:
        return (
            self.db.query(Integracao)
            .filter(Integracao.id == integracao_id, Integracao.user_id == user_id)
            .first()
        )

    def por_label(self, user_id: int, provedor: str, label: str) -> Optional[Integracao]:
        return (
            self.db.query(Integracao)
            .filter(Integracao.user_id == user_id,
                    Integracao.provedor == provedor,
                    Integracao.label == label)
            .first()
        )

    def upsert(self, user_id: int, provedor: str, label: str,
               credenciais: str, ativa: bool = True) -> Integracao:
        existente = self.por_label(user_id, provedor, label)
        if existente:
            existente.credenciais = credenciais
            existente.ativa = ativa
            existente.atualizado_em = datetime.now(timezone.utc)
            self.db.add(existente)
            self.db.flush()
            return existente
        nova = Integracao(user_id=user_id, provedor=provedor, label=label,
                          credenciais=credenciais, ativa=ativa)
        self.db.add(nova)
        self.db.flush()
        return nova

    def remover(self, integracao: Integracao) -> None:
        self.db.delete(integracao)
