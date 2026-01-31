import datetime
import hashlib
import logging
from typing import List, Optional

from fastapi import HTTPException, status

from app.models.dataset import Dataset
from app.models.click_row import ClickRow
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.services.csv_service import CSVService
from app.utils.serialization import normalize_raw_data

logger = logging.getLogger(__name__)


class ClickService:
    def __init__(self, dataset_repo: DatasetRepository, click_repo: ClickRowRepository):
        self.dataset_repo = dataset_repo
        self.click_repo = click_repo

    @staticmethod
    def _generate_click_hash(row_data: dict) -> str:
        """Gera hash para deduplicação de cliques."""
        components = [
            str(row_data.get("date") or ""),
            str(row_data.get("time") or ""),
            str(row_data.get("channel") or ""),
            str(row_data.get("clicks") or "0"),
            str(row_data.get("sub_id") or ""),
        ]
        row_str = "|".join(components)
        return hashlib.md5(row_str.encode()).hexdigest()

    def upload_click_csv(self, file_content: bytes, filename: str, user_id: int) -> Dataset:
        """Processa upload de CSV de cliques."""
        if not filename.endswith(".csv"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Apenas arquivos CSV são permitidos")

        df, errors = CSVService.validate_click_csv(file_content, filename)
        if df is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Erro ao processar CSV de cliques: {'; '.join(errors)}",
            )

        # Criar registro de dataset do tipo 'click'
        dataset = self.dataset_repo.create(Dataset(user_id=user_id, filename=filename, type="click"))

        rows_data = CSVService.dataframe_to_dict_list(df)
        
        # Otimização de busca de hashes
        all_dates = df['date'].unique()
        min_csv_date = min(all_dates) if len(all_dates) > 0 else None
        
        if min_csv_date:
            lookback_date = min_csv_date - datetime.timedelta(days=7)
            existing_hashes = self.click_repo.get_existing_hashes(user_id, min_date=lookback_date)
        else:
            existing_hashes = set()

        click_rows = []
        for row_data in rows_data:
            row_hash = self._generate_click_hash(row_data)
            
            if row_hash in existing_hashes:
                continue
                
            existing_hashes.add(row_hash)

            raw_data = row_data.get("raw_data")
            raw_data_json = normalize_raw_data(raw_data) if raw_data is not None else None

            click_rows.append(
                ClickRow(
                    dataset_id=dataset.id,
                    user_id=user_id,
                    date=row_data["date"],
                    time=row_data.get("time"),
                    channel=row_data.get("channel") or "Desconhecido",
                    sub_id=row_data.get("sub_id"),
                    clicks=int(row_data.get("clicks", 0)),
                    row_hash=row_hash,
                    raw_data=raw_data_json,
                )
            )

        if not click_rows and len(rows_data) > 0:
            logger.info(f"Todos os cliques do arquivo {filename} são duplicados.")

        self.click_repo.bulk_create(click_rows)
        self.dataset_repo.db.refresh(dataset)
        
        return dataset

    def list_latest_clicks(
        self,
        user_id: int,
        start_date: Optional[datetime.date] = None,
        end_date: Optional[datetime.date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ):
        """Lista cliques do último dataset carregado."""
        latest = (
            self.dataset_repo.db.query(Dataset)
            .filter(Dataset.user_id == user_id, Dataset.type == "click")
            .order_by(Dataset.uploaded_at.desc())
            .first()
        )
        if not latest:
            return []
            
        rows = self.click_repo.list_by_dataset(latest.id, user_id, start_date, end_date, limit, offset)
        return [self.serialize_click(r) for r in rows]

    def list_all_clicks(
        self,
        user_id: int,
        start_date: Optional[datetime.date] = None,
        end_date: Optional[datetime.date] = None,
        limit: Optional[int] = None,
        offset: int = 0,
    ):
        """Lista todos os cliques históricos."""
        rows = self.click_repo.list_by_user(user_id, start_date, end_date, limit, offset)
        return [self.serialize_click(r) for r in rows]

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
            "time": row.time.isoformat() if row.time else None,
            "channel": row.channel,
            "clicks": row.clicks,
            "sub_id": row.sub_id,
            "raw_data": row.raw_data,
        }
