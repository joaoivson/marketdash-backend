import datetime
import logging
import hashlib
from decimal import Decimal
from typing import List, Optional

import pandas as pd
import numpy as np
from fastapi import HTTPException, status

from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.services.csv_service import CSVService
from app.utils.serialization import serialize_value, clean_number

logger = logging.getLogger(__name__)


class DatasetService:
    def __init__(self, dataset_repo: DatasetRepository, row_repo: DatasetRowRepository):
        self.dataset_repo = dataset_repo
        self.row_repo = row_repo

    @staticmethod
    def _generate_row_hash(row_data: dict, user_id: int) -> str:
        """
        Gera um hash MD5 determinístico para o agrupamento.
        Inclui user_id e normaliza a data para evitar conflitos globais.
        """
        # Normalizar a data para string ISO format
        date_val = row_data.get("date")
        if hasattr(date_val, 'isoformat'):
            date_str = date_val.isoformat()
        else:
            date_str = str(date_val)

        components = [
            str(user_id),
            date_str,
            str(row_data.get("platform") or "nan").strip().lower(),
            str(row_data.get("category") or "nan").strip().lower(),
            str(row_data.get("product") or "nan").strip().lower(),
            str(row_data.get("status") or "nan").strip().lower(),
            str(row_data.get("sub_id1") or "nan").strip().lower(),
        ]
        row_str = "|".join(components)
        return hashlib.md5(row_str.encode()).hexdigest()

    def upload_csv(self, file_content: bytes, filename: str, user_id: int) -> tuple[Dataset, dict]:
        if not filename.endswith(".csv"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

        df, errors = CSVService.validate_csv(file_content, filename)
        if df is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro ao processar CSV: {'; '.join(errors)}",
            )

        # 1. Agrupamento e Consolidação (Groupby)
        # Tratar nulos para o groupby não descartar linhas
        group_cols = ['date', 'platform', 'category', 'product', 'status', 'sub_id1']
        for col in group_cols:
            if col in df.columns:
                df[col] = df[col].fillna('nan')
            else:
                df[col] = 'nan'

        # Garantir métricas numéricas
        metrics = ['revenue', 'commission', 'cost', 'quantity']
        for col in metrics:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 0

        # Agrupar
        df_grouped = df.groupby(group_cols, as_index=False).agg({
            'revenue': 'sum',
            'commission': 'sum',
            'cost': 'sum',
            'quantity': 'sum'
        })

        # 2. Deduplicação e Inserção
        # Buscar hashes existentes (últimos 120 dias para datasets)
        lookback_date = datetime.date.today() - datetime.timedelta(days=120)
        existing_hashes = self.row_repo.get_existing_hashes(user_id, min_date=lookback_date)

        dataset_rows_to_create = []
        rows_data = df_grouped.to_dict('records')
        ignored_count = 0
        total_rows = len(rows_data)
        
        for row_data in rows_data:
            # Restaurar 'nan' para None para salvar no banco
            row_clean = {}
            for col in group_cols:
                val = row_data[col]
                row_clean[col] = None if val == 'nan' else val
            
            row_hash = self._generate_row_hash(row_clean, user_id)
            
            if row_hash in existing_hashes:
                ignored_count += 1
                continue
                
            existing_hashes.add(row_hash)
            
            # Adicionar aos dados que serão criados depois
            dataset_rows_to_create.append({
                "clean_data": row_clean,
                "metrics": {
                    "revenue": float(row_data['revenue']),
                    "commission": float(row_data['commission']),
                    "cost": float(row_data['cost']),
                    "quantity": int(row_data["quantity"])
                },
                "row_hash": row_hash
            })

        # Bloqueio se tudo for duplicado
        if total_rows > 0 and ignored_count == total_rows:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, 
                detail="Todos os dados deste arquivo já foram importados anteriormente (100% duplicado)."
            )

        # Criar registro de dataset apenas se houver o que inserir
        dataset = self.dataset_repo.create(Dataset(user_id=user_id, filename=filename))

        dataset_rows = []
        for item in dataset_rows_to_create:
            row_clean = item["clean_data"]
            m = item["metrics"]
            profit = m["revenue"] - m["commission"] - m["cost"]

            dataset_rows.append(
                DatasetRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=row_clean["date"],
                    product=row_clean["product"],
                    platform=row_clean["platform"],
                    category=row_clean["category"],
                    status=row_clean["status"],
                    sub_id1=row_clean["sub_id1"],
                    revenue=m["revenue"],
                    commission=m["commission"],
                    cost=m["cost"],
                    profit=profit,
                    quantity=m["quantity"],
                    row_hash=item["row_hash"],
                )
            )

        if dataset_rows:
            self.row_repo.bulk_create(dataset_rows)
            if ignored_count > 0:
                logger.info(f"Deduplicação: {ignored_count} grupos de vendas foram ignorados pois já existem no banco.")

        self.dataset_repo.db.refresh(dataset)
        
        metadata = {
            "total_rows": total_rows,
            "inserted_rows": len(dataset_rows),
            "ignored_rows": ignored_count
        }
        
        return dataset, metadata

    def list_latest_rows(
        self,
        user_id: int,
        start_date: Optional[datetime.date],
        end_date: Optional[datetime.date],
        limit: Optional[int],
        offset: int,
    ):
        latest = self.dataset_repo.get_latest_by_user(user_id)
        if not latest:
            return []
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Data inicial não pode ser maior que a data final.")
        rows = self.row_repo.list_by_dataset(latest.id, user_id, start_date, end_date, limit, offset)
        return [self.serialize_row(r) for r in rows]

    def list_all_rows(
        self,
        user_id: int,
        start_date: Optional[datetime.date],
        end_date: Optional[datetime.date],
        limit: Optional[int],
        offset: int,
    ):
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Data inicial não pode ser maior que a data final.")
        rows = self.row_repo.list_by_user(user_id, start_date, end_date, limit, offset)
        return [self.serialize_row(r) for r in rows]

    def list_datasets(self, user_id: int):
        return self.dataset_repo.list_by_user(user_id)

    def get_dataset(self, dataset_id: int, user_id: int):
        dataset = self.dataset_repo.get_by_id(dataset_id, user_id)
        if not dataset:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dataset não encontrado")
        return dataset

    def list_dataset_rows(
        self,
        dataset_id: int,
        user_id: int,
        start_date: datetime.date,
        end_date: datetime.date,
    ):
        if start_date > end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Data inicial não pode ser maior que a data final.")
        rows = self.row_repo.list_by_dataset(dataset_id, user_id, start_date, end_date, None, 0)
        return [self.serialize_row(r) for r in rows]

    def serialize_row(self, row: DatasetRow) -> dict:
        return {
            "id": row.id,
            "dataset_id": row.dataset_id,
            "user_id": row.user_id,
            "date": row.date,
            "product": row.product,
            "platform": row.platform,
            "category": row.category,
            "status": row.status,
            "sub_id1": row.sub_id1,
            "revenue": serialize_value(row.revenue),
            "commission": serialize_value(row.commission),
            "cost": serialize_value(row.cost),
            "profit": serialize_value(row.profit),
            "quantity": row.quantity,
        }

    def apply_ad_spend(
        self, user_id: int, amount: float, sub_id1: Optional[str], db_session
    ):
        """Aplica valor de anúncio (cost) nas linhas de faturamento agrupadas."""
        latest = self.dataset_repo.get_latest_by_user(user_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhum dataset encontrado para o usuário")

        # Filtro base
        rows_query = db_session.query(DatasetRow).filter(
            DatasetRow.user_id == user_id,
            DatasetRow.dataset_id == latest.id
        )
        if sub_id1:
            rows_query = rows_query.filter(DatasetRow.sub_id1 == sub_id1)

        total_rows = rows_query.count()
        if total_rows == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhuma linha encontrada para aplicar o valor de anúncio")

        amount_per_row = Decimal(str(amount)) / Decimal(str(total_rows))
        
        # Como as linhas agora são agrupadas, o número de linhas é muito menor
        # Podemos aplicar o update diretamente
        batch = rows_query.all()
        mappings = []
        for row in batch:
            new_cost = (row.cost or 0) + amount_per_row
            new_profit = (row.revenue or 0) - (row.commission or 0) - new_cost
            mappings.append({
                "id": row.id, 
                "cost": new_cost,
                "profit": new_profit
            })
        
        db_session.bulk_update_mappings(DatasetRow, mappings)
        db_session.commit()

        return {
            "updated": len(batch),
            "dataset_id": latest.id,
            "sub_id1": sub_id1,
            "amount": amount,
        }

    def delete_all(self, user_id: int) -> dict:
        count = self.dataset_repo.delete_all_by_user(user_id)
        return {"deleted": count}
