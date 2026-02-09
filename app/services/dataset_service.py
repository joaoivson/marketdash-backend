import datetime
import logging
import hashlib
from datetime import date, datetime as dt
from decimal import Decimal
from typing import List, Optional

import pandas as pd
import numpy as np
from fastapi import HTTPException, status


def _ensure_date(value) -> date:
    """Garante um date válido; None/pd.NaT/inválido vira date.today()."""
    if value is None:
        return date.today()
    try:
        if getattr(pd, "isna", None) and pd.isna(value):
            return date.today()
    except (TypeError, ValueError):
        pass
    if isinstance(value, dt):
        return value.date()
    if isinstance(value, date):
        return value
    return date.today()

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

    def create_dataset(self, user_id: int, filename: str, type: str = "transaction") -> Dataset:
        """Cria um registro de dataset com status pendente."""
        dataset = Dataset(user_id=user_id, filename=filename, type=type, status="pending")
        return self.dataset_repo.create(dataset)

    @staticmethod
    def _generate_row_hash(row_data: dict, user_id: int) -> str:
        """
        Gera um hash MD5 determinístico para o registro de venda.
        Utiliza user_id + order_id + product_id para garantir unicidade por item de pedido.
        """
        components = [
            str(user_id),
            str(row_data.get("order_id") or "nan").strip().lower(),
            str(row_data.get("product_id") or "nan").strip().lower(),
        ]
        row_str = "|".join(components)
        return hashlib.md5(row_str.encode()).hexdigest()

    def process_commission_csv(self, dataset_id: int, user_id: int, file_content: bytes, filename: str) -> None:
        """
        Processa CSV de comissão para um dataset já criado (uso pela task Celery).
        Regra: em caso de row_hash existente, os dados do arquivo prevalecem (upsert).
        Atualiza dataset.status e dataset.row_count; em erro de validação define status='error'.
        """
        dataset = self.dataset_repo.get_by_id(dataset_id, user_id)
        if not dataset:
            logger.warning(f"process_commission_csv: dataset {dataset_id} not found for user {user_id}")
            return

        df, errors = CSVService.validate_csv(file_content, filename)
        if df is None:
            dataset.status = "error"
            dataset.error_message = "; ".join(errors[:10]) if errors else "Erro ao validar CSV"
            self.dataset_repo.db.commit()
            logger.error(f"Validation errors for dataset {dataset_id}: {errors}")
            return

        # 1. Agrupamento e Consolidação (Groupby)
        group_cols = ['date', 'platform', 'category', 'product', 'status', 'sub_id1', 'order_id', 'product_id']
        for col in group_cols:
            if col in df.columns:
                df[col] = df[col].fillna('nan')
            else:
                df[col] = 'nan'

        metrics = ['revenue', 'commission', 'cost', 'quantity']
        for col in metrics:
            if col in df.columns:
                if col == 'quantity':
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(1).astype(int)
                else:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 1 if col == 'quantity' else 0

        df_grouped = df.groupby(group_cols, as_index=False).agg({
            'revenue': 'sum',
            'commission': 'sum',
            'cost': 'sum',
            'quantity': 'sum'
        })

        # 2. Deduplicação e Inserção/Atualização
        existing_hashes = self.row_repo.get_existing_hashes(user_id)
        dataset_rows_to_create = []
        rows_data = df_grouped.to_dict('records')
        inserted_count = 0
        updated_count = 0
        processed_hashes_in_file = set()

        for row_data in rows_data:
            row_clean = {}
            for col in group_cols:
                val = row_data[col]
                row_clean[col] = None if val == 'nan' else val

            row_hash = self._generate_row_hash(row_clean, user_id)
            if row_hash in processed_hashes_in_file:
                continue
            processed_hashes_in_file.add(row_hash)

            if row_hash in existing_hashes:
                updated_count += 1
            else:
                inserted_count += 1

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

        dataset_rows = []
        for item in dataset_rows_to_create:
            row_clean = item["clean_data"]
            m = item["metrics"]
            profit = m["revenue"] - m["commission"] - m["cost"]
            row_date = _ensure_date(row_clean["date"])
            dataset_rows.append(
                DatasetRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=row_date,
                    product=row_clean["product"],
                    platform=row_clean["platform"],
                    category=row_clean["category"],
                    status=row_clean["status"],
                    sub_id1=row_clean["sub_id1"],
                    order_id=row_clean["order_id"],
                    product_id=row_clean["product_id"],
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
            dataset.row_count = inserted_count  # só linhas novas ficam com este dataset_id (upsert não altera dataset_id)
            dataset.status = "completed"
            self.dataset_repo.db.commit()
            logger.info(f"Processamento concluído: {inserted_count} novas linhas, {updated_count} atualizadas para dataset {dataset_id}.")

    def upload_csv(self, file_content: bytes, filename: str, user_id: int) -> tuple[Dataset, dict]:
        """
        Upload de CSV de comissão. Regra: linhas já existentes (mesmo row_hash) são atualizadas com os dados do arquivo.
        """
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
        group_cols = ['date', 'platform', 'category', 'product', 'status', 'sub_id1', 'order_id', 'product_id']
        for col in group_cols:
            if col in df.columns:
                df[col] = df[col].fillna('nan')
            else:
                df[col] = 'nan'

        # Garantir métricas numéricas e tipos corretos
        metrics = ['revenue', 'commission', 'cost', 'quantity']
        for col in metrics:
            if col in df.columns:
                if col == 'quantity':
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(1).astype(int)
                else:
                    df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
            else:
                df[col] = 1 if col == 'quantity' else 0

        # Agrupar
        df_grouped = df.groupby(group_cols, as_index=False).agg({
            'revenue': 'sum',
            'commission': 'sum',
            'cost': 'sum',
            'quantity': 'sum'
        })

        # 2. Deduplicação e Inserção/Atualização
        # Buscar hashes existentes para identificar o que é novo vs o que será atualizado
        existing_hashes = self.row_repo.get_existing_hashes(user_id)

        dataset_rows_to_create = []
        rows_data = df_grouped.to_dict('records')
        updated_count = 0
        inserted_count = 0
        processed_hashes_in_file = set()
        total_rows = len(rows_data)
        
        for row_data in rows_data:
            # Restaurar 'nan' para None para salvar no banco
            row_clean = {}
            for col in group_cols:
                val = row_data[col]
                row_clean[col] = None if val == 'nan' else val
            
            row_hash = self._generate_row_hash(row_clean, user_id)
            
            # Se já processamos este hash NESTE arquivo, pulamos para evitar conflitos no mesmo lote
            if row_hash in processed_hashes_in_file:
                continue
            
            processed_hashes_in_file.add(row_hash)

            if row_hash in existing_hashes:
                updated_count += 1
            else:
                inserted_count += 1
                
            # Adicionar aos dados que serão criados ou atualizados
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

        # Criar registro de dataset
        dataset = self.dataset_repo.create(Dataset(user_id=user_id, filename=filename))

        dataset_rows = []
        for item in dataset_rows_to_create:
            row_clean = item["clean_data"]
            m = item["metrics"]
            profit = m["revenue"] - m["commission"] - m["cost"]
            row_date = _ensure_date(row_clean["date"])
            dataset_rows.append(
                DatasetRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=row_date,
                    product=row_clean["product"],
                    platform=row_clean["platform"],
                    category=row_clean["category"],
                    status=row_clean["status"],
                    sub_id1=row_clean["sub_id1"],
                    order_id=row_clean["order_id"],
                    product_id=row_clean["product_id"],
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
            dataset.row_count = inserted_count  # só linhas novas ficam com este dataset_id (upsert não altera dataset_id)
            dataset.status = "completed"
            self.dataset_repo.db.commit()
            logger.info(f"Processamento concluído: {inserted_count} novas linhas, {updated_count} atualizadas.")

        self.dataset_repo.db.refresh(dataset)
        
        metadata = {
            "total_rows": total_rows,
            "inserted_rows": inserted_count,
            "updated_rows": updated_count,
            "ignored_rows": total_rows - (inserted_count + updated_count)
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
        # Último dataset de comissão (transaction), não o último de qualquer tipo (ex.: click)
        latest = self.dataset_repo.get_latest_by_user_and_type(user_id, "transaction")
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
            "order_id": row.order_id,
            "product_id": row.product_id,
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
