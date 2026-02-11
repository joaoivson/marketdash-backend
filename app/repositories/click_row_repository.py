import json
import time
from typing import Iterable, List, Optional
from datetime import date

from sqlalchemy.orm import Session
from sqlalchemy import func, case

from app.core.config import settings
from app.models.click_row import ClickRow


class ClickRowRepository:
    def __init__(self, db: Session):
        self.db = db

    def bulk_create(self, rows: Iterable[ClickRow]) -> None:
        """
        Inserção em lote de cliques com ON CONFLICT DO UPDATE (upsert).
        Regra fundamental: dados do arquivo prevalecem; linhas existentes são atualizadas.
        """
        rows_list = list(rows)
        if not rows_list:
            return

        from sqlalchemy.dialects.postgresql import insert

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
                'time': getattr(row, 'time', None),
            }
            mappings.append(mapping)

        # Em conflito: mesmo dataset (ex.: chunks do mesmo job) -> soma clicks; outro dataset -> substitui.
        # Assim upload por chunks acumula; re-upload de outro arquivo substitui totais por (date, channel).
        stmt = insert(ClickRow).values(mappings)
        stmt = stmt.on_conflict_do_update(
            index_elements=['row_hash'],
            set_={
                'clicks': case(
                    (ClickRow.dataset_id == stmt.excluded.dataset_id, ClickRow.clicks + stmt.excluded.clicks),
                    else_=stmt.excluded.clicks,
                ),
                'date': stmt.excluded.date,
                'channel': stmt.excluded.channel,
                'sub_id': stmt.excluded.sub_id,
                'time': func.coalesce(stmt.excluded.time, ClickRow.time),
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

    def get_total_clicks(
        self,
        user_id: int,
        dataset_id: Optional[int] = None,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> int:
        """Retorna a soma de clicks no mesmo escopo das listagens (user_id, opcionalmente dataset_id e datas)."""
        query = self.db.query(func.coalesce(func.sum(ClickRow.clicks), 0)).filter(
            ClickRow.user_id == user_id
        )
        if dataset_id is not None:
            query = query.filter(ClickRow.dataset_id == dataset_id)
        if start_date:
            query = query.filter(ClickRow.date >= start_date)
        if end_date:
            query = query.filter(ClickRow.date <= end_date)
        result = query.scalar()
        return int(result) if result is not None else 0

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

    def list_aggregated_by_dataset(
        self,
        dataset_id: int,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[dict]:
        """Lista cliques por (date, channel, sub_id); rows[].clicks = total do grupo; time = primeira hora do grupo."""
        sum_clicks = func.sum(ClickRow.clicks).label("clicks")
        time_min = func.min(ClickRow.time).label("time")
        query = self.db.query(
            ClickRow.date,
            ClickRow.channel,
            ClickRow.sub_id,
            sum_clicks,
            time_min,
        ).filter(
            ClickRow.user_id == user_id,
            ClickRow.dataset_id == dataset_id
        )
        if start_date:
            query = query.filter(ClickRow.date >= start_date)
        if end_date:
            query = query.filter(ClickRow.date <= end_date)
        query = query.group_by(ClickRow.date, ClickRow.channel, ClickRow.sub_id)
        query = query.order_by(ClickRow.date.desc(), sum_clicks.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        result = query.all()
        # #region agent log
        try:
            with open(settings.effective_debug_log_path, "a") as _f:
                _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_row_repo.list_aggregated_by_dataset", "message": "rows assembled", "data": {"dataset_id": dataset_id, "user_id": user_id, "rows_len": len(result)}, "hypothesisId": "H5"}) + "\n")
        except Exception:
            pass
        # #endregion
        return result

    def list_aggregated_by_user(
        self,
        user_id: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> List[dict]:
        """Lista cliques por (date, channel, sub_id) de todos os datasets do usuário; time = primeira hora do grupo."""
        sum_clicks = func.sum(ClickRow.clicks).label("clicks")
        time_min = func.min(ClickRow.time).label("time")
        query = self.db.query(
            ClickRow.date,
            ClickRow.channel,
            ClickRow.sub_id,
            sum_clicks,
            time_min,
        ).filter(ClickRow.user_id == user_id)
        if start_date:
            query = query.filter(ClickRow.date >= start_date)
        if end_date:
            query = query.filter(ClickRow.date <= end_date)
        query = query.group_by(ClickRow.date, ClickRow.channel, ClickRow.sub_id)
        query = query.order_by(ClickRow.date.desc(), sum_clicks.desc())
        if limit:
            query = query.limit(limit).offset(offset)
        result = query.all()
        # #region agent log
        try:
            with open(settings.effective_debug_log_path, "a") as _f:
                _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_row_repo.list_aggregated_by_user", "message": "rows assembled", "data": {"user_id": user_id, "rows_len": len(result)}, "hypothesisId": "H6"}) + "\n")
        except Exception:
            pass
        # #endregion
        return result

    def delete_all_by_user(self, user_id: int) -> int:
        """Deleta todos os cliques do usuário."""
        query = self.db.query(ClickRow).filter(ClickRow.user_id == user_id)
        count = query.count()
        query.delete()
        self.db.commit()
        return count
