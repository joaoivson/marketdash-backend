import logging

from app.tasks.celery_app import celery_app
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.db.session import SessionLocal
from app.services.dataset_service import DatasetService
from app.services.click_service import ClickService

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=3)
def process_csv_task(self, dataset_id: int, user_id: int, file_content: bytes, filename: str):
    """
    Background task to process commission/sales CSV (groupby, dedup, bulk_create).
    Uses DatasetService.process_commission_csv for same business logic as sync upload.
    """
    db = SessionLocal()
    try:
        logger.info(f"Starting commission processing for dataset {dataset_id} (user {user_id})")
        dataset_repo = DatasetRepository(db)
        row_repo = DatasetRowRepository(db)
        service = DatasetService(dataset_repo, row_repo)
        service.process_commission_csv(dataset_id, user_id, file_content, filename)
        logger.info(f"Commission processing completed for dataset {dataset_id}")
        return {"status": "completed"}
    except Exception as exc:
        logger.error(f"Error processing commission dataset {dataset_id}: {exc}")
        try:
            dataset_repo = DatasetRepository(db)
            dataset = dataset_repo.get_by_id(dataset_id, user_id)
            if dataset:
                dataset.status = "error"
                dataset.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3)
def process_click_csv_task(self, dataset_id: int, user_id: int, file_content: bytes, filename: str):
    """
    Background task to process click CSV (groupby, dedup, bulk_create).
    Uses ClickService.process_click_csv for same business logic as sync upload.
    """
    db = SessionLocal()
    try:
        logger.info(f"Starting click processing for dataset {dataset_id} (user {user_id})")
        dataset_repo = DatasetRepository(db)
        click_repo = ClickRowRepository(db)
        service = ClickService(dataset_repo, click_repo)
        service.process_click_csv(dataset_id, user_id, file_content, filename)
        logger.info(f"Click processing completed for dataset {dataset_id}")
        return {"status": "completed"}
    except Exception as exc:
        logger.error(f"Error processing click dataset {dataset_id}: {exc}")
        try:
            dataset_repo = DatasetRepository(db)
            dataset = dataset_repo.get_by_id(dataset_id, user_id)
            if dataset:
                dataset.status = "error"
                dataset.error_message = str(exc)
                db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
