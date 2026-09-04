"""Acesso ao vínculo Sub ID ↔ campanha de grupos (espelho da 080)."""
from typing import Dict, List

from sqlalchemy.orm import Session

from app.models.campanha_grupos import Campanha, CampanhaSubId


class CampanhaSubIdRepository:
    def __init__(self, db: Session):
        self.db = db

    def sub_ids(self, campanha_id: int) -> List[str]:
        return [
            s.sub_id
            for s in self.db.query(CampanhaSubId)
            .filter(CampanhaSubId.campanha_id == campanha_id)
            .order_by(CampanhaSubId.sub_id)
            .all()
        ]

    def campanha_por_sub_id(self, user_id: int) -> Dict[str, int]:
        """sub_id → campanha_id, para a tela dizer O QUE trava um vínculo.

        Filtra por usuária no JOIN: `sub_id` é texto livre e a mesma string
        pode existir em contas diferentes — sem o JOIN, uma afiliada veria o
        vínculo da outra.
        """
        linhas = (
            self.db.query(CampanhaSubId.sub_id, CampanhaSubId.campanha_id)
            .join(Campanha, Campanha.id == CampanhaSubId.campanha_id)
            .filter(Campanha.user_id == user_id)
            .all()
        )
        return {sub_id: campanha_id for sub_id, campanha_id in linhas}

    def definir(self, campanha_id: int, sub_ids: List[str]) -> None:
        """Substitui o conjunto inteiro — mesmo contrato do PUT de anúncios."""
        atuais = {
            s.sub_id: s
            for s in self.db.query(CampanhaSubId)
            .filter(CampanhaSubId.campanha_id == campanha_id)
            .all()
        }
        desejados = set(sub_ids)
        for sub_id in desejados - set(atuais):
            self.db.add(CampanhaSubId(campanha_id=campanha_id, sub_id=sub_id))
        for sub_id in set(atuais) - desejados:
            self.db.delete(atuais[sub_id])
