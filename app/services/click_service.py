import datetime
import hashlib
import json
import logging
import time
from typing import List, Optional, Tuple

import pandas as pd
from fastapi import HTTPException, status
from sqlalchemy import func

from app.models.dataset import Dataset
from app.models.click_row import ClickRow
from app.repositories.dataset_repository import DatasetRepository
from app.core.config import settings
from app.repositories.click_row_repository import ClickRowRepository
from app.services.csv_service import CSVService

logger = logging.getLogger(__name__)


class ClickService:
    def __init__(self, dataset_repo: DatasetRepository, click_repo: ClickRowRepository):
        self.dataset_repo = dataset_repo
        self.click_repo = click_repo

    @staticmethod
    def _generate_click_hash(row_data: dict, user_id: int) -> str:
        """Unicidade por (user_id, date, channel, sub_id)."""
        date_val = row_data.get("date")
        if hasattr(date_val, "isoformat"):
            date_str = date_val.isoformat()
        else:
            date_str = str(date_val)
        sub_id_val = row_data.get("sub_id")
        sub_id_str = (str(sub_id_val).strip().lower() if sub_id_val not in (None, "") else "")
        components = [
            str(user_id),
            date_str,
            str(row_data.get("channel") or "Desconhecido").strip().lower(),
            sub_id_str,
        ]
        return hashlib.md5("|".join(components).encode()).hexdigest()

    def upload_click_csv(self, file_content: bytes, filename: str, user_id: int) -> Tuple[Dataset, dict]:
        """Processa upload de CSV de cliques com agrupamento por (date, channel). total_clicks = linhas do CSV; rows.clicks = soma por dia/canal."""
        if not filename.endswith(".csv"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

        df, errors = CSVService.validate_click_csv(file_content, filename)
        if df is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro ao processar CSV de cliques: {'; '.join(errors)}",
            )

        # 1. Agrupamento por (date, channel, sub_id): total_clicks = cada linha do CSV; rows.clicks = total por grupo
        total_original_rows = len(df)
        if "sub_id" not in df.columns:
            df["sub_id"] = None
        agg_dict = {"clicks": "sum"}
        if "time" in df.columns:
            agg_dict["time"] = "first"
        df_grouped = df.groupby(["date", "channel", "sub_id"], as_index=False).agg(agg_dict)

        existing_hashes = self.click_repo.get_existing_hashes(user_id)
        rows_to_create = []
        rows_data = df_grouped.to_dict("records")
        inserted_count = 0
        updated_count = 0
        processed_hashes_in_file = set()

        for row_data in rows_data:
            sub_id_val = row_data.get("sub_id")
            if sub_id_val is not None and isinstance(sub_id_val, str) and sub_id_val.strip() == "":
                sub_id_val = None
            if sub_id_val is not None and not isinstance(sub_id_val, str):
                sub_id_val = str(sub_id_val).strip() or None
            row_data_clean = {"date": row_data["date"], "channel": row_data["channel"], "sub_id": sub_id_val, "clicks": row_data["clicks"]}
            row_hash = self._generate_click_hash(row_data_clean, user_id)
            if row_hash in processed_hashes_in_file:
                continue
            processed_hashes_in_file.add(row_hash)
            if row_hash in existing_hashes:
                updated_count += 1
            else:
                inserted_count += 1
            row_data_clean["time"] = row_data.get("time")
            rows_to_create.append({**row_data_clean, "row_hash": row_hash})

        dataset = self.dataset_repo.create(Dataset(user_id=user_id, filename=filename, type="click"))

        click_rows = []
        for item in rows_to_create:
            click_rows.append(
                ClickRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=item["date"],
                    time=item.get("time"),
                    channel=item["channel"],
                    sub_id=item.get("sub_id"),
                    clicks=int(item["clicks"]),
                    row_hash=item["row_hash"],
                )
            )

        if click_rows:
            self.click_repo.bulk_create(click_rows)
            dataset.row_count = total_original_rows
            dataset.status = "completed"
            self.dataset_repo.db.commit()
            # #region agent log
            try:
                with open(settings.effective_debug_log_path, "a") as _f:
                    _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_service.upload_click_csv", "message": "total_clicks/rows: upload sync", "data": {"total_original_rows": total_original_rows, "rows_to_create_len": len(rows_to_create), "dataset_id": dataset.id, "dataset_row_count": dataset.row_count}, "hypothesisId": "H1"}) + "\n")
            except Exception:
                pass
            # #endregion
            logger.info(
                f"Upload cliques: {total_original_rows} linhas CSV -> {len(rows_to_create)} rows (date, channel), "
                f"{inserted_count} novos, {updated_count} atualizados para dataset {dataset.id}."
            )

        self.dataset_repo.db.refresh(dataset)

        metadata = {
            "total_rows": total_original_rows,
            "inserted_rows": inserted_count,
            "updated_rows": updated_count,
            "ignored_rows": total_original_rows - (inserted_count + updated_count),
        }
        return dataset, metadata

    def process_click_csv(self, dataset_id: int, user_id: int, file_content: bytes, filename: str) -> None:
        """
        Processa CSV de cliques para um dataset já criado (uso pela task Celery).
        Regra: dados do arquivo prevalecem (upsert). Atualiza dataset.status e dataset.row_count.
        """
        dataset = self.dataset_repo.get_by_id(dataset_id, user_id)
        if not dataset:
            logger.warning(f"process_click_csv: dataset {dataset_id} not found for user {user_id}")
            return

        df, errors = CSVService.validate_click_csv(file_content, filename)
        if df is None:
            dataset.status = "error"
            dataset.error_message = "; ".join(errors[:10]) if errors else "Erro ao validar CSV de cliques"
            self.dataset_repo.db.commit()
            logger.error(f"Validation errors for click dataset {dataset_id}: {errors}")
            return

        total_original_rows = len(df)
        if "sub_id" not in df.columns:
            df["sub_id"] = None
        agg_dict = {"clicks": "sum"}
        if "time" in df.columns:
            agg_dict["time"] = "first"
        df_grouped = df.groupby(["date", "channel", "sub_id"], as_index=False).agg(agg_dict)

        existing_hashes = self.click_repo.get_existing_hashes(user_id)
        rows_to_create = []
        rows_data = df_grouped.to_dict("records")
        inserted_count = 0
        updated_count = 0
        processed_hashes_in_file = set()

        for row_data in rows_data:
            sub_id_val = row_data.get("sub_id")
            if sub_id_val is not None and isinstance(sub_id_val, str) and sub_id_val.strip() == "":
                sub_id_val = None
            if sub_id_val is not None and not isinstance(sub_id_val, str):
                sub_id_val = str(sub_id_val).strip() or None
            row_data_clean = {"date": row_data["date"], "channel": row_data["channel"], "sub_id": sub_id_val, "clicks": row_data["clicks"]}
            row_hash = self._generate_click_hash(row_data_clean, user_id)
            if row_hash in processed_hashes_in_file:
                continue
            processed_hashes_in_file.add(row_hash)
            if row_hash in existing_hashes:
                updated_count += 1
            else:
                inserted_count += 1
            row_data_clean["time"] = row_data.get("time")
            rows_to_create.append({**row_data_clean, "row_hash": row_hash})

        click_rows = []
        for item in rows_to_create:
            click_rows.append(
                ClickRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=item["date"],
                    time=item.get("time"),
                    channel=item["channel"],
                    sub_id=item.get("sub_id"),
                    clicks=int(item["clicks"]),
                    row_hash=item["row_hash"],
                )
            )

        if click_rows:
            self.click_repo.bulk_create(click_rows)
            # IMPORTANTE: row_count deve refletir TODAS as linhas originais do CSV
            dataset.row_count = total_original_rows
            dataset.status = "completed"
            self.dataset_repo.db.commit()
            # #region agent log
            try:
                with open(settings.effective_debug_log_path, "a") as _f:
                    _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_service.process_click_csv", "message": "total_clicks/rows: process Celery", "data": {"total_original_rows": total_original_rows, "rows_to_create_len": len(rows_to_create), "dataset_id": dataset_id, "dataset_row_count": dataset.row_count}, "hypothesisId": "H2"}) + "\n")
            except Exception:
                pass
            # #endregion
            logger.info(
                f"Processamento cliques: {total_original_rows} linhas CSV -> {len(rows_to_create)} rows (date, channel), "
                f"{inserted_count} novos, {updated_count} atualizados para dataset {dataset_id}."
            )

    def list_latest_clicks(
        self,
        user_id: int,
        start_date: Optional[datetime.date] = None,
        end_date: Optional[datetime.date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ):
        """Lista cliques do último dataset concluído. Só considera status=completed para evitar rows vazios enquanto o worker processa."""
        latest = (
            self.dataset_repo.db.query(Dataset)
            .filter(Dataset.user_id == user_id, Dataset.type == "click", Dataset.status == "completed")
            .order_by(Dataset.uploaded_at.desc())
            .first()
        )
        if not latest:
            # #region agent log
            try:
                with open(settings.effective_debug_log_path, "a") as _f:
                    _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_service.list_latest_clicks", "message": "no latest dataset", "data": {"user_id": user_id}, "hypothesisId": "H3"}) + "\n")
            except Exception:
                pass
            # #endregion
            return {"total_clicks": 0, "rows": []}
        
        # total_clicks = número de linhas originais do CSV (dataset.row_count)
        # Cada linha do CSV representa 1 clique individual
        total_clicks = latest.row_count if latest.row_count else 0
        
        # Rows são AGREGADOS para exibição (Hybrid approach)
        rows = self.click_repo.list_aggregated_by_dataset(latest.id, user_id, start_date, end_date, limit, offset)
        serialized = [self._serialize_aggregated_click(r) for r in rows]
        # #region agent log
        try:
            with open(settings.effective_debug_log_path, "a") as _f:
                _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_service.list_latest_clicks", "message": "total_clicks and rows built", "data": {"user_id": user_id, "latest_dataset_id": latest.id, "latest_row_count": getattr(latest, "row_count", None), "total_clicks": total_clicks, "rows_len": len(rows), "serialized_len": len(serialized)}, "hypothesisId": "H3"}) + "\n")
        except Exception:
            pass
        # #endregion
        return {
            "total_clicks": total_clicks,
            "rows": serialized,
        }

    def list_all_clicks(
        self,
        user_id: int,
        start_date: Optional[datetime.date] = None,
        end_date: Optional[datetime.date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ):
        """Lista todos os cliques históricos. Retorna total_clicks (soma) e rows."""
        # total_clicks = soma dos row_count de TODOS os datasets de cliques completos
        # Cada dataset.row_count = número de linhas originais do CSV
        total_clicks_query = (
            self.dataset_repo.db.query(func.coalesce(func.sum(Dataset.row_count), 0))
            .filter(
                Dataset.user_id == user_id,
                Dataset.type == "click",
                Dataset.status == "completed"
            )
        )
        total_clicks = int(total_clicks_query.scalar() or 0)
        
        # Rows são AGREGADOS para exibição (Hybrid approach)
        rows = self.click_repo.list_aggregated_by_user(user_id, start_date, end_date, limit, offset)
        serialized = [self._serialize_aggregated_click(r) for r in rows]
        # #region agent log
        try:
            with open(settings.effective_debug_log_path, "a") as _f:
                _f.write(json.dumps({"timestamp": int(time.time() * 1000), "location": "click_service.list_all_clicks", "message": "total_clicks and rows built", "data": {"user_id": user_id, "total_clicks": total_clicks, "rows_len": len(rows), "serialized_len": len(serialized)}, "hypothesisId": "H4"}) + "\n")
        except Exception:
            pass
        # #endregion
        return {
            "total_clicks": total_clicks,
            "rows": serialized,
        }

    def delete_all_clicks(self, user_id: int) -> dict:
        """Remove todos os dados de cliques do usuário."""
        # Deletar datasets de cliques (isso limpa click_rows via cascade)
        self.dataset_repo.db.query(Dataset).filter(
            Dataset.user_id == user_id, 
            Dataset.type == "click"
        ).delete()
        self.dataset_repo.db.commit()
        return {"status": "success", "message": "Todos os dados de cliques foram removidos."}

    def serialize_click(self, row: ClickRow) -> dict:
        """Serializa um ClickRow para resposta da API."""
        return {
            "id": row.id,
            "dataset_id": row.dataset_id,
            "user_id": row.user_id,
            "date": row.date,
            "channel": row.channel,
            "clicks": row.clicks,
            "sub_id": row.sub_id,
        }

    def _serialize_aggregated_click(self, row) -> dict:
        """Serializa o resultado da agregação (date, channel, clicks, time). time em HH:MM:SS para a API."""
        t = getattr(row, "time", None)
        if t is not None and hasattr(t, "strftime"):
            t = t.strftime("%H:%M:%S")
        return {
            "id": None,
            "dataset_id": None,
            "user_id": None,
            "date": getattr(row, "date", None),
            "time": t,
            "channel": getattr(row, "channel", None) or "",
            "sub_id": getattr(row, "sub_id", None),
            "clicks": int(row.clicks) if row.clicks else 0,
        }
