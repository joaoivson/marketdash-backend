import datetime
import math
import hashlib
from decimal import Decimal
from typing import List, Optional

import pandas as pd
from fastapi import HTTPException, status

from app.models.dataset import Dataset
from app.models.dataset_row import DatasetRow
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.services.csv_service import CSVService
from app.utils.serialization import normalize_raw_data, serialize_value, clean_number
# Cache removido - agora é gerenciado pelo frontend via localStorage


class DatasetService:
    def __init__(self, dataset_repo: DatasetRepository, row_repo: DatasetRowRepository):
        self.dataset_repo = dataset_repo
        self.row_repo = row_repo

    @staticmethod
    def _generate_row_hash(row_data: dict) -> str:
        """
        Gera um hash MD5 determinístico para uma linha do dataset baseando-se em campos chave.
        Isso permite identificar duplicatas mesmo em uploads diferentes.
        """
        # Campos que definem a unicidade de uma transação
        components = [
            str(row_data.get("date") or ""),
            str(row_data.get("time") or ""),
            str(row_data.get("product") or ""),
            str(row_data.get("revenue") or "0"),
            str(row_data.get("commission") or "0"),
            str(row_data.get("platform") or ""),
            str(row_data.get("status") or ""),
            str(row_data.get("sub_id1") or ""),
        ]
        # Juntar com um separador e gerar hash
        row_str = "|".join(components)
        return hashlib.md5(row_str.encode()).hexdigest()

    def upload_csv(self, file_content: bytes, filename: str, user_id: int) -> Dataset:
        if not filename.endswith(".csv"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

        df, errors = CSVService.validate_csv(file_content, filename)
        if df is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro ao processar CSV: {'; '.join(errors)}",
            )

        dataset = self.dataset_repo.create(Dataset(user_id=user_id, filename=filename))

        rows_data = CSVService.dataframe_to_dict_list(df)
        
        # Identificar intervalo de datas para buscar hashes existentes (otimização)
        all_dates = df['date'].unique()
        min_csv_date = min(all_dates) if len(all_dates) > 0 else None
        
        # Buscar hashes existentes no banco para este usuário
        # Limitamos a busca a partir da data mínima presente no CSV (ou 90 dias atrás por segurança)
        if min_csv_date:
            lookback_date = min_csv_date - datetime.timedelta(days=7) # Pequena margem de segurança
            existing_hashes = self.row_repo.get_existing_hashes(user_id, min_date=lookback_date)
        else:
            existing_hashes = set()

        def _sanitize(value):
            if value is None:
                return None
            try:
                if isinstance(value, float) and math.isnan(value):
                    return None
            except Exception:
                pass
            try:
                if pd.isna(value):
                    return None
            except Exception:
                pass
            if isinstance(value, pd.Timestamp):
                return value.to_pydatetime().isoformat()
            if isinstance(value, (datetime.date, datetime.datetime, datetime.time)):
                return value.isoformat()
            if isinstance(value, Decimal):
                return float(value)
            if hasattr(value, "item"):
                try:
                    return value.item()
                except Exception:
                    return value
            return value

        dataset_rows = []
        for row_data in rows_data:
            raw_data = row_data.get("raw_data") if isinstance(row_data, dict) else None
            raw_data_json = normalize_raw_data(raw_data) if raw_data is not None else raw_data

            # Garantir que campos numéricos sejam None ou numéricos válidos
            def safe_numeric(value, default=None):
                if value is None or pd.isna(value):
                    return default
                try:
                    if isinstance(value, (int, float)):
                        return float(value) if value is not None else default
                    # Tentar converter string para número
                    if isinstance(value, str):
                        cleaned = value.replace("R$", "").replace(" ", "").replace(",", ".")
                        return float(cleaned) if cleaned else default
                    return float(value) if value is not None else default
                except (ValueError, TypeError):
                    return default

            def safe_int(value, default=None):
                if value is None or pd.isna(value):
                    return default
                try:
                    return int(float(value)) if value is not None else default
                except (ValueError, TypeError):
                    return default

            def safe_str(value):
                if value is None:
                    return None
                text = str(value).strip()
                return text or None

            # Gerar hash para a linha atual
            row_hash = self._generate_row_hash(row_data)
            
            # Pular se já existir no banco (deduplicação)
            if row_hash in existing_hashes:
                continue
                
            # Adicionar ao set de hashes existentes para evitar duplicatas dentro do próprio CSV
            existing_hashes.add(row_hash)

            dataset_rows.append(
                DatasetRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=row_data["date"],
                    time=row_data.get("time"),
                    product=row_data["product"],
                    platform=safe_str(row_data.get("platform")),
                    revenue=safe_numeric(row_data.get("revenue"), 0),
                    cost=safe_numeric(row_data.get("cost"), 0),
                    commission=safe_numeric(row_data.get("commission"), 0),
                    profit=safe_numeric(row_data.get("profit"), 0),
                    status=safe_str(row_data.get("status")),
                    category=safe_str(row_data.get("category")),
                    sub_id1=safe_str(row_data.get("sub_id1")),
                    mes_ano=safe_str(row_data.get("mes_ano")),
                    # Campos numéricos opcionais - garantir None se não existirem
                    gross_value=safe_numeric(row_data.get("gross_value")),
                    commission_value=safe_numeric(row_data.get("commission_value")),
                    net_value=safe_numeric(row_data.get("net_value")),
                    quantity=safe_int(row_data.get("quantity"), 1),  # Default 1 se não existir
                    row_hash=row_hash,
                    raw_data=raw_data_json,
                )
            )

        # Se todas as linhas foram filtradas como duplicadas
        if not dataset_rows and len(rows_data) > 0:
            # Poderíamos deletar o dataset vazio ou apenas informar
            # Por enquanto, vamos manter o dataset mas ele não terá linhas
            logger.info(f"Todas as {len(rows_data)} linhas do arquivo {filename} são duplicadas e foram ignoradas.")
            # Opcional: raise HTTPException(status_code=400, detail="Todas as linhas do arquivo já existem no banco de dados.")

        # bulk_create faz commit, então o dataset também será commitado
        self.row_repo.bulk_create(dataset_rows)
        # Refresh dataset para garantir que uploaded_at esteja disponível após commit
        # (uploaded_at é gerado pelo banco com server_default=func.now())
        self.dataset_repo.db.refresh(dataset)
        # Cache removido - frontend gerencia via localStorage
        return dataset

    def list_latest_rows(
        self,
        user_id: int,
        start_date: Optional[datetime.date],
        end_date: Optional[datetime.date],
        include_raw_data: bool,
        limit: Optional[int],
        offset: int,
    ):
        # Cache removido - frontend gerencia via localStorage
        latest = self.dataset_repo.get_latest_by_user(user_id)
        if not latest:
            return []
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Data inicial não pode ser maior que a data final.")
        rows = self.row_repo.list_by_dataset(latest.id, start_date, end_date, limit, offset)
        payload = [self.serialize_row(r, include_raw_data=include_raw_data) for r in rows]
        return payload

    def list_all_rows(
        self,
        user_id: int,
        start_date: Optional[datetime.date],
        end_date: Optional[datetime.date],
        include_raw_data: bool,
        limit: Optional[int],
        offset: int,
    ):
        # Cache removido - frontend gerencia via localStorage
        if start_date and end_date and start_date > end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Data inicial não pode ser maior que a data final.")
        rows = self.row_repo.list_by_user(user_id, start_date, end_date, limit, offset)
        payload = [self.serialize_row(r, include_raw_data=include_raw_data) for r in rows]
        return payload

    def list_datasets(self, user_id: int):
        return self.dataset_repo.list_by_user(user_id)

    def get_dataset(self, dataset_id: int, user_id: int):
        """Busca dataset por ID, sempre filtrando por user_id PRIMEIRO para garantir isolamento de dados."""
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
        include_raw_data: bool,
    ):
        if start_date > end_date:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Data inicial não pode ser maior que a data final.")
        if (end_date - start_date).days > 90:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="O intervalo máximo permitido é de 90 dias.")
        # Cache removido - frontend gerencia via localStorage
        # Sempre filtrar por user_id PRIMEIRO para garantir isolamento de dados
        rows = self.row_repo.list_by_dataset(dataset_id, user_id, start_date, end_date, None, 0)
        payload = [self.serialize_row(r, include_raw_data=include_raw_data) for r in rows]
        return payload

    def serialize_row(self, row: DatasetRow, include_raw_data: bool = True) -> dict:
        raw_data = row.raw_data if include_raw_data else None
        if include_raw_data and isinstance(raw_data, dict):
            raw_data = self._compact_raw_data(raw_data)
        # Converter time para string ISO ou None para evitar problemas de validação do Pydantic
        time_str = None
        if row.time is not None:
            time_str = row.time.isoformat()
        return {
            "id": row.id,
            "dataset_id": row.dataset_id,
            "user_id": row.user_id,
            "date": row.date,  # Manter como date object - Pydantic aceita
            "transaction_date": row.transaction_date if row.transaction_date else None,
            "time": time_str,  # Converter para string ISO ou None
            "product": row.product,
            "product_name": row.product_name,
            "platform": row.platform,
            "revenue": serialize_value(row.revenue),
            "cost": serialize_value(row.cost),
            "commission": serialize_value(row.commission),
            "profit": serialize_value(row.profit),
            "gross_value": serialize_value(row.gross_value),
            "commission_value": serialize_value(row.commission_value),
            "net_value": serialize_value(row.net_value),
            "quantity": serialize_value(row.quantity),
            "status": row.status,
            "category": row.category,
            "sub_id1": row.sub_id1,
            "mes_ano": row.mes_ano,
            "raw_data": raw_data,
        }

    @staticmethod
    def _compact_raw_data(raw: dict) -> dict:
        keep_exact = {
            "Valor de Compra(R$)",
            "Comissão líquida do afiliado(R$)",
            "Comissão do Item da Shopee(R$)",
            "Valor gasto anuncios",
            "ID do pedido",
            "ID do Pedido",
        }
        keep_contains = (
            "id do pedido",
            "comissao",
            "comissão",
            "valor de compra",
            "valor gasto",
        )
        compact = {}
        for key, value in raw.items():
            if key in keep_exact:
                compact[key] = value
                continue
            lower = key.lower()
            if any(token in lower for token in keep_contains):
                compact[key] = value
        return compact

    def apply_ad_spend(
        self, user_id: int, amount: float, sub_id1: Optional[str], db_session
    ):
        latest = self.dataset_repo.get_latest_by_user(user_id)
        if not latest:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhum dataset encontrado para o usuário")

        # Sempre filtrar por user_id PRIMEIRO para garantir isolamento de dados
        rows_query = db_session.query(DatasetRow).filter(
            DatasetRow.user_id == user_id,
            DatasetRow.dataset_id == latest.id
        )
        if sub_id1:
            rows_query = rows_query.filter(DatasetRow.sub_id1 == sub_id1)

        rows_query = rows_query.order_by(DatasetRow.id)
        total_rows = rows_query.count()
        if total_rows == 0:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nenhuma linha encontrada para aplicar o valor de anúncio")

        amount_per_row = amount / total_rows
        batch_size = 1000
        processed = 0
        updated = 0

        while processed < total_rows:
            batch = rows_query.limit(batch_size).offset(processed).all()
            if not batch:
                break
            mappings = []
            for row in batch:
                raw = dict(row.raw_data) if row.raw_data else {}
                prev = raw.get("Valor gasto anuncios")
                prev_val = clean_number(prev) or 0
                raw["Valor gasto anuncios"] = prev_val + amount_per_row
                mappings.append({"id": row.id, "raw_data": raw})
                updated += 1
            db_session.bulk_update_mappings(DatasetRow, mappings)
            db_session.commit()
            processed += len(batch)

        # Cache removido - frontend gerencia via localStorage
        return {
            "updated": updated,
            "dataset_id": latest.id,
            "sub_id1": sub_id1,
            "amount": amount,
        }

    def delete_all(self, user_id: int) -> dict:
        """Deleta todos os datasets de um usuário e retorna a quantidade deletada."""
        count = self.dataset_repo.delete_all_by_user(user_id)
        # As linhas (DatasetRow) serão deletadas automaticamente via CASCADE
        # Cache removido - frontend gerencia via localStorage
        return {"deleted": count}
