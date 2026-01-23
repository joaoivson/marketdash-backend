from typing import Iterable, List, Optional
from datetime import date

from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow


class DatasetRowRepository:
    def __init__(self, db: Session):
        self.db = db

    def bulk_create(self, rows: Iterable[DatasetRow]) -> None:
        """
        Bulk insert usando bulk_insert_mappings para melhor controle de tipos e performance.
        """
        rows_list = list(rows)
        if not rows_list:
            return
        
        # Converter objetos DatasetRow para dicionários com apenas os campos necessários
        # Isso garante que os tipos sejam corretos e a ordem seja respeitada
        mappings = []
        for row in rows_list:
            mapping = {
                'dataset_id': row.dataset_id,
                'user_id': row.user_id,
                'date': row.date,
                'transaction_date': row.transaction_date,
                'time': row.time,
                'product': row.product,
                'product_name': row.product_name,
                'platform': row.platform,
                'status': row.status,
                'category': row.category,
                'sub_id1': row.sub_id1,  # String - garantir que não seja convertido
                'mes_ano': row.mes_ano,
                'raw_data': row.raw_data,
                'revenue': row.revenue,
                'cost': row.cost,
                'commission': row.commission,
                'profit': row.profit,
                'gross_value': row.gross_value,
                'commission_value': row.commission_value,
                'net_value': row.net_value,
                'quantity': row.quantity,
            }
            mappings.append(mapping)
        
        # Usar bulk_insert_mappings para inserção em lote com controle explícito de tipos
        self.db.bulk_insert_mappings(DatasetRow, mappings)
        self.db.commit()

    def list_by_dataset(
        self,
        dataset_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[DatasetRow]:
        query = self.db.query(DatasetRow).filter(DatasetRow.dataset_id == dataset_id)
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
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Buscando dataset_rows para user_id={user_id}, start_date={start_date}, end_date={end_date}, limit={limit}, offset={offset}")
        
        # Verificar total de registros no banco para este user_id
        total_count = self.db.query(DatasetRow).filter(DatasetRow.user_id == user_id).count()
        logger.info(f"Total de dataset_rows encontrados para user_id={user_id}: {total_count}")
        
        # Verificar todos os user_ids únicos no banco (para debug)
        all_user_ids = self.db.query(DatasetRow.user_id).distinct().all()
        logger.info(f"User IDs únicos encontrados no banco: {[uid[0] for uid in all_user_ids]}")
        
        query = self.db.query(DatasetRow).filter(DatasetRow.user_id == user_id)
        if start_date:
            query = query.filter(DatasetRow.date >= start_date)
        if end_date:
            query = query.filter(DatasetRow.date <= end_date)
        query = query.order_by(DatasetRow.date.desc(), DatasetRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        
        results = query.all()
        logger.info(f"Registros retornados após filtros: {len(results)}")
        return results
