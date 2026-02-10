import base64
import logging
import os
from pathlib import Path
from typing import Optional

from app.tasks.celery_app import celery_app
from app.repositories.dataset_repository import DatasetRepository
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.click_row_repository import ClickRowRepository
from app.db.session import SessionLocal
from app.services.dataset_service import DatasetService
from app.services.click_service import ClickService
from app.core.config import settings
from app.services.storage import download_file, delete_object

logger = logging.getLogger(__name__)


def _get_file_content(
    file_path: Optional[str] = None,
    file_content_b64: Optional[str] = None,
    storage_key: Optional[str] = None,
) -> bytes:
    """
    Obtém bytes do CSV a partir de: storage (S3), caminho em disco ou base64.
    Preferência: storage_key (worker não precisa de disco compartilhado), file_path, file_content_b64.
    """
    if storage_key:
        bucket = settings.S3_BUCKET
        if not bucket:
            raise ValueError("S3_BUCKET not set; cannot use storage_key")
        content = download_file(bucket, storage_key)
        if not content:
            raise FileNotFoundError(f"Upload file not found in storage: {storage_key}")
        try:
            delete_object(bucket, storage_key)
        except Exception as e:
            logger.warning(f"Could not delete temp object {storage_key}: {e}")
        return content
    if file_path:
        path = Path(file_path)
        if path.exists():
            content = path.read_bytes()
            try:
                path.unlink()
            except OSError as e:
                logger.warning(f"Could not remove temp file {file_path}: {e}")
            return content
        raise FileNotFoundError(f"Upload temp file not found: {file_path}")
    if file_content_b64:
        return base64.b64decode(file_content_b64)
    raise ValueError("One of file_path, file_content_b64 or storage_key must be provided")


@celery_app.task(bind=True, max_retries=3, soft_time_limit=3600, time_limit=3700)
def process_csv_task(
    self,
    dataset_id: int,
    user_id: int,
    filename: str,
    file_path: Optional[str] = None,
    file_content_b64: Optional[str] = None,
    storage_key: Optional[str] = None,
):
    """
    Processa CSV de comissão/vendas (groupby, dedup, bulk_create).
    Suporta: storage_key (download S3), file_path (disco compartilhado) ou file_content_b64.
    """
    db = SessionLocal()
    try:
        file_content = _get_file_content(file_path=file_path, file_content_b64=file_content_b64, storage_key=storage_key)
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
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(bind=True, max_retries=3, soft_time_limit=3600, time_limit=3700)
def process_click_csv_task(
    self,
    dataset_id: int,
    user_id: int,
    filename: str,
    file_path: Optional[str] = None,
    file_content_b64: Optional[str] = None,
    storage_key: Optional[str] = None,
):
    """
    Processa CSV de cliques (groupby, dedup, bulk_create).
    Suporta: storage_key (download S3), file_path (disco compartilhado) ou file_content_b64.
    """
    db = SessionLocal()
    try:
        file_content = _get_file_content(file_path=file_path, file_content_b64=file_content_b64, storage_key=storage_key)
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
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
