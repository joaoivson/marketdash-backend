from typing import Any, Dict, Iterable, List, Optional
from datetime import date

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.dataset_row import DatasetRow


class DatasetRowRepository:
    def __init__(self, db: Session):
        self.db = db

    def bulk_create(self, rows: Iterable[DatasetRow], commit: bool = True) -> None:
        """
        Bulk insert usando ON CONFLICT DO NOTHING para evitar erros de constraint único.

        commit=False: executa o INSERT mas NÃO commita — usado pelo re-sync atômico da Shopee,
        que faz DELETE+REINSERT em lotes numa única transação (commit único no fim) pra o
        dashboard nunca enxergar o estado intermediário vazio.
        """
        rows_list = list(rows)
        if not rows_list:
            return
        
        # Converter objetos DatasetRow para dicionários com apenas os campos necessários
        mappings = []
        for row in rows_list:
            mapping = {
                'dataset_id': row.dataset_id,
                'user_id': row.user_id,
                'date': row.date,
                'time': row.time,
                'platform': row.platform,
                'channel': row.channel,
                'category': row.category,
                'product': row.product,
                'status': row.status,
                'attribution_type': row.attribution_type,
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
                'time': stmt.excluded.time,
                'sub_id1': stmt.excluded.sub_id1,
                'category': stmt.excluded.category,
                'platform': stmt.excluded.platform,
                'product': stmt.excluded.product,
                'channel': stmt.excluded.channel,
                'attribution_type': stmt.excluded.attribution_type,
            }
        )
        
        self.db.execute(stmt)
        if commit:
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

    # Colunas que a API expõe (DatasetRowResponse). Consultar por COLUNA em vez de
    # entidade evita materializar um objeto ORM por linha — na conta maior são 67 mil
    # linhas por request, e o custo de construir a entidade (identity map, tracking de
    # estado) é várias vezes o de ler a tupla. `serialize_row` só lê atributos, então
    # a Row nomeada serve igual.
    _COLUNAS_DA_API = (
        DatasetRow.id,
        DatasetRow.dataset_id,
        DatasetRow.user_id,
        DatasetRow.date,
        DatasetRow.time,
        DatasetRow.product,
        DatasetRow.platform,
        DatasetRow.category,
        DatasetRow.status,
        DatasetRow.channel,
        DatasetRow.attribution_type,
        DatasetRow.sub_id1,
        DatasetRow.order_id,
        DatasetRow.product_id,
        DatasetRow.revenue,
        DatasetRow.commission,
        DatasetRow.cost,
        DatasetRow.profit,
        DatasetRow.quantity,
    )

    def list_by_user(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[Any]:
        """Lista linhas de datasets do usuário, sempre filtrando por user_id para garantir isolamento de dados."""
        query = self.db.query(*self._COLUNAS_DA_API).filter(DatasetRow.user_id == user_id)
        if start_date:
            query = query.filter(DatasetRow.date >= start_date)
        if end_date:
            query = query.filter(DatasetRow.date <= end_date)
        query = query.order_by(DatasetRow.date.desc(), DatasetRow.id.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        
        return query.all()

    def count_by_date(
        self, user_id: int, platform: str, start_date: date, end_date: date
    ) -> Dict[date, int]:
        """Contagem de rows por dia — usada pela guarda de fetch suspeito de parcial (sync Shopee).

        Compara o que já está persistido ANTES de um upsert com o que a busca atual trouxe,
        pra sinalizar (sem bloquear) uma execução que trouxe visivelmente menos que o esperado.
        """
        rows = (
            self.db.query(DatasetRow.date, func.count(DatasetRow.id))
            .filter(
                DatasetRow.user_id == user_id,
                DatasetRow.platform == platform,
                DatasetRow.date >= start_date,
                DatasetRow.date <= end_date,
            )
            .group_by(DatasetRow.date)
            .all()
        )
        return {d: c for d, c in rows}

    def get_existing_order_item_keys(
        self, user_id: int, platform: Optional[str] = None
    ) -> set:
        """Retorna conjunto de (order_id, item_id) já salvos para o usuário.
        Usado para evitar duplicatas entre CSV e API sync."""
        query = self.db.query(DatasetRow.order_id, DatasetRow.product_id).filter(
            DatasetRow.user_id == user_id,
            DatasetRow.order_id.isnot(None),
            DatasetRow.product_id.isnot(None),
        )
        if platform:
            query = query.filter(DatasetRow.platform == platform)
        rows = query.all()
        return {(r[0], r[1]) for r in rows}

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
