from typing import List, Optional
from datetime import date

from fastapi import HTTPException, status

from app.models.ad_spend import AdSpend
# Cache removido - agora é gerenciado pelo frontend via localStorage
from app.repositories.ad_spend_repository import AdSpendRepository


class AdSpendService:
    @staticmethod
    def _serialize(item: AdSpend) -> dict:
        return {
            "id": item.id,
            "date": item.date,
            "amount": item.amount,
            "sub_id": item.sub_id,
            "clicks": item.clicks or 0,
        }
    def __init__(self, repo: AdSpendRepository):
        self.repo = repo

    def create(self, user_id: int, date: date, amount: float, sub_id: Optional[str], clicks: Optional[int] = 0) -> AdSpend:
        sub_id_val = None if sub_id in ["", "__all__"] else sub_id
        ad_spend = AdSpend(user_id=user_id, date=date, sub_id=sub_id_val, amount=amount, clicks=clicks or 0)
        created = self.repo.create(ad_spend)
        # Cache removido - frontend gerencia via localStorage
        return created

    def bulk_create(self, user_id: int, items) -> List[AdSpend]:
        if not items:
            return []
        ad_spends = []
        for item in items:
            sub_id_val = None if item.sub_id in ["", "__all__"] else item.sub_id
            ad_spends.append(
                AdSpend(user_id=user_id, date=item.date, sub_id=sub_id_val, amount=item.amount, clicks=getattr(item, 'clicks', 0) or 0)
            )
        created = self.repo.bulk_create(ad_spends)
        # Cache removido - frontend gerencia via localStorage
        return created

    def list(
        self,
        user_id: int,
        start_date: Optional[date],
        end_date: Optional[date],
        limit: Optional[int],
        offset: int,
    ) -> List[AdSpend]:
        # Cache removido - frontend gerencia via localStorage
        data = self.repo.list_by_user(user_id, start_date, end_date, limit, offset)
        payload = [self._serialize(item) for item in data]
        return payload

    def update(self, user_id: int, ad_spend_id: int, payload) -> AdSpend:
        ad_spend = self.repo.get_by_id(ad_spend_id, user_id)
        if not ad_spend:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro não encontrado")
        if payload.date is not None:
            ad_spend.date = payload.date
        if payload.amount is not None:
            ad_spend.amount = payload.amount
        if payload.clicks is not None:
            ad_spend.clicks = payload.clicks
        if payload.sub_id is not None:
            ad_spend.sub_id = None if payload.sub_id in ["", "__all__"] else payload.sub_id
        self.repo.db.commit()
        self.repo.db.refresh(ad_spend)
        # Cache removido - frontend gerencia via localStorage
        return ad_spend

    def delete(self, user_id: int, ad_spend_id: int) -> None:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Tentando deletar ad_spend_id={ad_spend_id} para user_id={user_id}")
        
        # Sempre filtrar por user_id PRIMEIRO para garantir isolamento de dados
        ad_spend = self.repo.get_by_id(ad_spend_id, user_id)
        if not ad_spend:
            logger.warning(f"Ad_spend {ad_spend_id} não encontrado para user_id={user_id}")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registro não encontrado")
        
        logger.info(f"Deletando ad_spend_id={ad_spend_id} do user_id={user_id}")
        self.repo.delete(ad_spend)
        # Cache removido - frontend gerencia via localStorage

    def delete_all(self, user_id: int) -> dict:
        """Deleta todos os ad_spends de um usuário e retorna a quantidade deletada."""
        count = self.repo.delete_all_by_user(user_id)
        # Cache removido - frontend gerencia via localStorage
        return {"deleted": count}
