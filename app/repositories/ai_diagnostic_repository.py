"""Acesso às sessões de diagnóstico e suas mensagens."""
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.ai_diagnostic import (
    STATUS_GERANDO, AiDiagnostic, AiDiagnosticMessage,
)


class AiDiagnosticRepository:
    def __init__(self, db: Session):
        self.db = db

    def criar(self, user_id: int, inicio: date, fim: date,
              snapshot: Dict[str, Any]) -> AiDiagnostic:
        sessao = AiDiagnostic(
            user_id=user_id, periodo_inicio=inicio, periodo_fim=fim,
            snapshot=snapshot, status=STATUS_GERANDO,
        )
        self.db.add(sessao)
        self.db.commit()
        self.db.refresh(sessao)
        return sessao

    def salvar(self, sessao: AiDiagnostic) -> AiDiagnostic:
        self.db.commit()
        self.db.refresh(sessao)
        return sessao

    def buscar(self, diagnostic_id: int, user_id: int) -> Optional[AiDiagnostic]:
        return (
            self.db.query(AiDiagnostic)
            .filter(AiDiagnostic.id == diagnostic_id, AiDiagnostic.user_id == user_id)
            .first()
        )

    def em_andamento(self, user_id: int) -> Optional[AiDiagnostic]:
        return (
            self.db.query(AiDiagnostic)
            .filter(AiDiagnostic.user_id == user_id, AiDiagnostic.status == STATUS_GERANDO)
            .first()
        )

    def listar(self, user_id: int, limite: int = 30) -> List[AiDiagnostic]:
        return (
            self.db.query(AiDiagnostic)
            .filter(AiDiagnostic.user_id == user_id)
            .order_by(AiDiagnostic.criado_em.desc())
            .limit(limite)
            .all()
        )

    def adicionar_mensagem(self, diagnostic_id: int, papel: str,
                           conteudo: str) -> AiDiagnosticMessage:
        m = AiDiagnosticMessage(diagnostic_id=diagnostic_id, papel=papel, conteudo=conteudo)
        self.db.add(m)
        self.db.commit()
        self.db.refresh(m)
        return m

    def listar_mensagens(self, diagnostic_id: int) -> List[AiDiagnosticMessage]:
        return (
            self.db.query(AiDiagnosticMessage)
            .filter(AiDiagnosticMessage.diagnostic_id == diagnostic_id)
            .order_by(AiDiagnosticMessage.criado_em.asc())
            .all()
        )

    def contar_mensagens_da_usuaria(self, diagnostic_id: int) -> int:
        return (
            self.db.query(AiDiagnosticMessage)
            .filter(
                AiDiagnosticMessage.diagnostic_id == diagnostic_id,
                AiDiagnosticMessage.papel == "user",
            )
            .count()
        )
