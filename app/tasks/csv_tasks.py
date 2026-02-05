import logging
import pandas as pd
import io
from app.tasks.celery_app import celery_app
from app.services.csv_service import CSVService
from app.repositories.dataset_row_repository import DatasetRowRepository
from app.repositories.dataset_repository import DatasetRepository
from app.db.session import SessionLocal
from app.models.dataset_row import DatasetRow

logger = logging.getLogger(__name__)

@celery_app.task(bind=True, max_retries=3)
def process_csv_task(self, dataset_id: int, user_id: int, file_content: bytes, filename: str):
    """
    Background task to process and validate CSV data.
    """
    db = SessionLocal()
    try:
        logger.info(f"Starting processing for dataset {dataset_id} (user {user_id})")
        
        # 1. Validate and clean CSV using existing CSVService
        df, errors = CSVService.validate_csv(file_content, filename)
        
        # We only stop if df is None (critical error)
        # Low-level warnings (missing columns prefilled with 0) shouldn't block ingestion
        if df is not None:
            # 2. Convert DataFrame to DatasetRow objects
            rows = []
            
            # List of valid columns to avoid TypeError (e.g., 'time', 'mes_ano' are not in the model)
            valid_columns = {c.name for c in DatasetRow.__table__.columns if not c.primary_key}

            for _, row_data in df.iterrows():
                row_dict = row_data.to_dict()
                
                # Ensure date is a date object
                if isinstance(row_dict.get('date'), str):
                    row_dict['date'] = pd.to_datetime(row_dict['date']).date()
                
                # Filter out fields not in the model
                filtered_row = {k: v for k, v in row_dict.items() if k in valid_columns}
                
                rows.append(DatasetRow(
                    dataset_id=dataset_id,
                    user_id=user_id,
                    **filtered_row
                ))
            
            # 3. Bulk insert rows using repository
            # DatasetRowRepository.bulk_create handles UPSERT (on conflict do update)
            DatasetRowRepository(db).bulk_create(rows)
            
            # 4. Update dataset status
            dataset_repo = DatasetRepository(db)
            dataset = dataset_repo.get_by_id(dataset_id, user_id)
            if dataset:
                dataset.status = "completed"
                dataset.row_count = len(rows)
                db.commit()
            
            logger.info(f"Successfully processed {len(rows)} rows for dataset {dataset_id}")
            return {"status": "completed", "rows": len(rows)}
        else:
            # Handle validation errors
            dataset_repo = DatasetRepository(db)
            dataset = dataset_repo.get_by_id(dataset_id, user_id)
            if dataset:
                dataset.status = "error"
                dataset.error_message = "; ".join(errors[:5]) # Store first 5 errors
                db.commit()
            
            logger.error(f"Validation errors for dataset {dataset_id}: {errors}")
            return {"status": "error", "errors": errors}
            
    except Exception as exc:
        logger.error(f"Error processing dataset {dataset_id}: {exc}")
        # Update status to error on unhandled exception
        try:
            dataset_repo = DatasetRepository(db)
            dataset = dataset_repo.get_by_id(dataset_id, user_id)
            if dataset:
                dataset.status = "error"
                dataset.error_message = str(exc)
                db.commit()
        except:
            pass
        
        # Retry logic
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
