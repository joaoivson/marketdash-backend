from typing import Iterable, List, Optional
from datetime import date

from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow


class DatasetRowRepository:
    def __init__(self, db: Session):
        self.db = db

    def bulk_create(self, rows: Iterable[DatasetRow]) -> None:
        """
        Bulk insert usando ON CONFLICT DO NOTHING para evitar erros de constraint único.
        """
        rows_list = list(rows)
        if not rows_list:
            return
        
        # Converter objetos DatasetRow para dicionários com apenas os campos necessários
        mappings = []
        for row in rows_list:
            row_date = row.date
            if row_date is None:
                row_date = date.today()
            mapping = {
                'dataset_id': row.dataset_id,
                'user_id': row.user_id,
                'date': row_date,
                'platform': row.platform,
                'category': row.category,
                'product': row.product,
                'status': row.status,
                'sub_id1': row.sub_id1,
                'order_id': row.order_id,
                'product_id': row.product_id,
                'revenue': row.revenue,
                'commission': row.commission,
                'cost': row.cost,
                'profit': row.profit,
                'quantity': row.quantity,
                'row_hash': row.row_hash,
            }
            mappings.append(mapping)
        
        # Usar inserção com UPSERT (ON CONFLICT DO UPDATE) via SQLAlchemy Core (PostgreSQL)
        from sqlalchemy.dialects.postgresql import insert
        
        stmt = insert(DatasetRow).values(mappings)
        # Em conflito: atualizar apenas métricas/dimensões; NÃO atualizar dataset_id.
        # Assim, re-enviar um arquivo com dados já existentes não "transfere" linhas para o novo
        # dataset e os totais (ex.: listar por último dataset) não mudam indevidamente.
        stmt = stmt.on_conflict_do_update(
            index_elements=['row_hash'],
            set_={
                'status': stmt.excluded.status,
                'revenue': stmt.excluded.revenue,
                'commission': stmt.excluded.commission,
                'cost': stmt.excluded.cost,
                'profit': stmt.excluded.profit,
                'quantity': stmt.excluded.quantity,
                'date': stmt.excluded.date,
                'sub_id1': stmt.excluded.sub_id1,
                'category': stmt.excluded.category,
                'platform': stmt.excluded.platform,
                'product': stmt.excluded.product,
            }
        )
        
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
    ) -> List[DatasetRow]:
        """Lista linhas de um dataset, sempre filtrando por user_id PRIMEIRO para garantir isolamento de dados."""
        # Sempre filtrar por user_id PRIMEIRO para garantir isolamento de dados
        query = self.db.query(DatasetRow).filter(
            DatasetRow.user_id == user_id,
            DatasetRow.dataset_id == dataset_id
        )
        if start_date:
            query = query.filter(DatasetRow.date >= start_date)
        if end_date:
            query = query.filter(DatasetRow.date <= end_date)
        query = query.order_by(DatasetRow.date.desc(), DatasetRow.id.desc())
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
    ) -> List[DatasetRow]:
        """Lista linhas de datasets do usuário, sempre filtrando por user_id para garantir isolamento de dados."""
        query = self.db.query(DatasetRow).filter(DatasetRow.user_id == user_id)
        if start_date:
            query = query.filter(DatasetRow.date >= start_date)
        if end_date:
            query = query.filter(DatasetRow.date <= end_date)
        query = query.order_by(DatasetRow.date.desc(), DatasetRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        
        return query.all()

    def get_existing_hashes(self, user_id: int, min_date: Optional[date] = None) -> set:
        """Retorna um conjunto de hashes existentes para um usuário (sem limite de data)."""
        query = self.db.query(DatasetRow.row_hash).filter(
            DatasetRow.user_id == user_id,
            DatasetRow.row_hash.isnot(None)
        )
        # Removido limite de data para garantir deduplicação completa
        # if min_date:
        #     query = query.filter(DatasetRow.date >= min_date)
        
        hashes = {r[0] for r in query.all()}
        return hashes
