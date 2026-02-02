from typing import Iterable, List, Optional
from datetime import date

from sqlalchemy.orm import Session

from app.models.click_row import ClickRow


class ClickRowRepository:
    def __init__(self, db: Session):
        self.db = db

    def bulk_create(self, rows: Iterable[ClickRow]) -> None:
        """
        Inserção em lote de cliques com ON CONFLICT DO NOTHING.
        """
        rows_list = list(rows)
        if not rows_list:
            return
        
        mappings = []
        for row in rows_list:
            mapping = {
                'dataset_id': row.dataset_id,
                'user_id': row.user_id,
                'date': row.date,
                'channel': row.channel,
                'sub_id': row.sub_id,
                'clicks': row.clicks,
                'row_hash': row.row_hash,
            }
            mappings.append(mapping)
        
        # Usar inserção com ON CONFLICT DO NOTHING via SQLAlchemy Core (PostgreSQL)
        from sqlalchemy.dialects.postgresql import insert
        
        stmt = insert(ClickRow).values(mappings)
        stmt = stmt.on_conflict_do_nothing(index_elements=['row_hash'])
        
        self.db.execute(stmt)
        self.db.commit()

    def list_by_dataset(
        self,
        dataset_id: int,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[ClickRow]:
        """Lista cliques de um dataset específico."""
        query = self.db.query(ClickRow).filter(
            ClickRow.user_id == user_id,
            ClickRow.dataset_id == dataset_id
        )
        if start_date:
            query = query.filter(ClickRow.date >= start_date)
        if end_date:
            query = query.filter(ClickRow.date <= end_date)
        query = query.order_by(ClickRow.date.desc(), ClickRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        return query.all()

    def list_by_user(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[ClickRow]:
        """Lista todos os cliques do usuário."""
        query = self.db.query(ClickRow).filter(ClickRow.user_id == user_id)
        if start_date:
            query = query.filter(ClickRow.date >= start_date)
        if end_date:
            query = query.filter(ClickRow.date <= end_date)
        query = query.order_by(ClickRow.date.desc(), ClickRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        return query.all()

    def get_existing_hashes(self, user_id: int, min_date: Optional[date] = None) -> set:
        """Retorna hashes existentes para deduplicação (sem limite de data)."""
        query = self.db.query(ClickRow.row_hash).filter(
            ClickRow.user_id == user_id,
            ClickRow.row_hash.isnot(None)
        )
        # Removido limite de data para garantir deduplicação completa
        # if min_date:
        #     query = query.filter(ClickRow.date >= min_date)
        
        return {r[0] for r in query.all()}

    def delete_all_by_user(self, user_id: int) -> int:
        """Deleta todos os cliques do usuário."""
        query = self.db.query(ClickRow).filter(ClickRow.user_id == user_id)
        count = query.count()
        query.delete()
        self.db.commit()
        return count
