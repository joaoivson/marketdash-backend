"""Acesso a templates de mensagem e suas variações."""
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.roteiro import TemplateMensagem, TemplateVariacao


class TemplateRepository:
    def __init__(self, db: Session):
        self.db = db

    def por_usuario(self, user_id: int, incluir_inativos: bool = False) -> List[TemplateMensagem]:
        q = self.db.query(TemplateMensagem).filter(TemplateMensagem.user_id == user_id)
        if not incluir_inativos:
            q = q.filter(TemplateMensagem.ativo.is_(True))
        return q.order_by(TemplateMensagem.criado_em.desc()).all()

    def por_id(self, user_id: int, template_id: int) -> Optional[TemplateMensagem]:
        return (
            self.db.query(TemplateMensagem)
            .filter(TemplateMensagem.id == template_id,
                    TemplateMensagem.user_id == user_id)
            .first()
        )

    def variacoes(self, template_id: int) -> List[TemplateVariacao]:
        return (
            self.db.query(TemplateVariacao)
            .filter(TemplateVariacao.template_id == template_id)
            .order_by(TemplateVariacao.id)
            .all()
        )

    def variacoes_por_template(self, template_ids: List[int]) -> Dict[int, List[TemplateVariacao]]:
        """Lote — a listagem mostra a contagem de variações sem N+1."""
        if not template_ids:
            return {}
        linhas = (
            self.db.query(TemplateVariacao)
            .filter(TemplateVariacao.template_id.in_(template_ids))
            .order_by(TemplateVariacao.id)
            .all()
        )
        agrupado: Dict[int, List[TemplateVariacao]] = {}
        for v in linhas:
            agrupado.setdefault(v.template_id, []).append(v)
        return agrupado

    def remover_variacoes(self, template_id: int) -> None:
        self.db.query(TemplateVariacao).filter(
            TemplateVariacao.template_id == template_id
        ).delete(synchronize_session=False)

    def adicionar(self, obj):
        self.db.add(obj)
        self.db.flush()
        return obj

    def marcar_tocado(self, template: TemplateMensagem) -> None:
        template.atualizado_em = datetime.now(timezone.utc)
        self.db.add(template)
