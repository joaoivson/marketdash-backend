"""Acesso às sessões de diagnóstico e suas mensagens."""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.ai_diagnostic import (
    PAPEL_USUARIA, STATUS_ERRO, STATUS_GERANDO, AiDiagnostic, AiDiagnosticMessage,
)

# Índice parcial da migration 044: uma sessão "gerando" por usuária.
INDICE_GERACAO_UNICA = "ux_ai_diagnostics_gerando_por_usuario"


class GeracaoDuplicada(Exception):
    """O banco recusou: já havia uma geração em andamento para esta usuária."""


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
        try:
            self.db.commit()
        except IntegrityError as e:
            # Perdemos a corrida com outra requisição da mesma usuária. Quem
            # decide o que isso significa é o serviço; aqui só traduzimos o
            # erro do banco para algo que não seja SQLAlchemy.
            self.db.rollback()
            if INDICE_GERACAO_UNICA in str(getattr(e, "orig", e)):
                raise GeracaoDuplicada() from e
            raise
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

    def expirar_travadas(self, user_id: int, limite: datetime) -> int:
        """
        Encerra sessões "gerando" criadas antes de `limite`.

        Geração dura segundos e sempre termina em pronto/erro — sobrou em
        "gerando" porque o processo morreu no meio. Sem isso a usuária fica
        presa num 409 para sempre, e o índice único da 044 nunca libera.
        """
        n = (
            self.db.query(AiDiagnostic)
            .filter(
                AiDiagnostic.user_id == user_id,
                AiDiagnostic.status == STATUS_GERANDO,
                AiDiagnostic.criado_em < limite,
            )
            .update(
                {
                    AiDiagnostic.status: STATUS_ERRO,
                    AiDiagnostic.erro_mensagem: "Análise interrompida. Tente de novo.",
                    AiDiagnostic.concluido_em: datetime.now(timezone.utc),
                },
                synchronize_session=False,
            )
        )
        if n:
            self.db.commit()
        return n

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

    def adicionar_mensagens(self, diagnostic_id: int,
                            mensagens: List[Tuple[str, str]]) -> List[AiDiagnosticMessage]:
        # Um único commit para todas: no chat isso grava pergunta+resposta juntas,
        # então uma falha no meio do caminho não deixa a pergunta da usuária
        # gravada sem a resposta correspondente (ou as duas entram, ou nenhuma).
        objetos = [
            AiDiagnosticMessage(diagnostic_id=diagnostic_id, papel=papel, conteudo=conteudo)
            for papel, conteudo in mensagens
        ]
        for m in objetos:
            self.db.add(m)
        self.db.commit()
        for m in objetos:
            self.db.refresh(m)
        return objetos

    def listar_mensagens(self, diagnostic_id: int) -> List[AiDiagnosticMessage]:
        return (
            self.db.query(AiDiagnosticMessage)
            .filter(AiDiagnosticMessage.diagnostic_id == diagnostic_id)
            # id desempata: pergunta e resposta entram no MESMO commit e podem
            # dividir o timestamp, o que inverteria a conversa na tela.
            .order_by(AiDiagnosticMessage.criado_em.asc(), AiDiagnosticMessage.id.asc())
            .all()
        )

    def contar_mensagens_da_usuaria(self, diagnostic_id: int) -> int:
        return (
            self.db.query(AiDiagnosticMessage)
            .filter(
                AiDiagnosticMessage.diagnostic_id == diagnostic_id,
                AiDiagnosticMessage.papel == PAPEL_USUARIA,
            )
            .count()
        )
