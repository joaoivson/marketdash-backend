from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.dataset import Dataset


class DatasetRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, dataset: Dataset) -> Dataset:
        self.db.add(dataset)
        self.db.flush()
        return dataset

    def get_latest_by_user(self, user_id: int) -> Optional[Dataset]:
        return (
            self.db.query(Dataset)
            .filter(Dataset.user_id == user_id)
            .order_by(Dataset.uploaded_at.desc())
            .first()
        )

    def list_by_user(self, user_id: int) -> List[Dataset]:
        return (
            self.db.query(Dataset)
            .filter(Dataset.user_id == user_id)
            .order_by(Dataset.uploaded_at.desc())
            .all()
        )

    def get_by_id(self, dataset_id: int) -> Optional[Dataset]:
        return self.db.query(Dataset).filter(Dataset.id == dataset_id).first()

    def delete_all_by_user(self, user_id: int) -> int:
        """Deleta todos os datasets de um usuário e retorna a quantidade deletada."""
        count = self.db.query(Dataset).filter(Dataset.user_id == user_id).count()
        self.db.query(Dataset).filter(Dataset.user_id == user_id).delete()
        self.db.commit()
        return count
