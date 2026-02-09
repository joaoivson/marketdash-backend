"""
Celery tasks for the jobs pipeline: split CSV into chunks and process each chunk.
Observability: structured logs (job_id, chunk_index, rows_processed, duration).
"""
import logging
import time
from io import BytesIO
from uuid import UUID

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

CHUNK_LINES = 20_000


@celery_app.task(bind=True, max_retries=2, soft_time_limit=600, time_limit=660)
def split_and_enqueue_chunks(self, job_id: str):
    """
    Download CSV from storage, split into chunks (Polars preferred), upload chunk files to storage,
    insert job_chunks, enqueue process_chunk for each. Update job total_chunks and status.
    """
    from app.db.session import SessionLocal
    from app.repositories.job_repository import JobRepository
    from app.models.job import JobChunk
    from app.services.storage import download_file, upload_file_obj, is_storage_configured
    from app.core.config import settings

    if not is_storage_configured():
        logger.error("split_and_enqueue_chunks: storage not configured")
        return

    db = SessionLocal()
    try:
        uid = UUID(job_id)
        job_repo = JobRepository(db)
        job = job_repo.get_by_id(uid)
        if not job:
            logger.warning(f"split_and_enqueue_chunks: job {job_id} not found")
            return

        bucket = settings.S3_BUCKET
        content = download_file(bucket, job.storage_key)
        if not content:
            logger.error(f"split_and_enqueue_chunks: failed to download {job.storage_key}")
            job.status = "error"
            db.commit()
            return

        use_polars = False
        try:
            import polars as pl
            use_polars = True
        except ImportError:
            pass

        chunk_index = 0
        if use_polars:
            try:
                reader = pl.read_csv_batched(BytesIO(content), batch_size=CHUNK_LINES)
                while True:
                    batches = reader.next_batches(1)
                    if not batches:
                        break
                    for batch_df in batches:
                        if batch_df.height == 0:
                            continue
                        chunk_bytes = batch_df.write_csv().encode("utf-8")
                        chunk_key = f"jobs/{job_id}/chunks/{chunk_index}.csv"
                        if not upload_file_obj(bucket, chunk_key, BytesIO(chunk_bytes), content_type="text/csv"):
                            logger.error(f"Failed to upload chunk {chunk_index}")
                            break
                        jc = JobChunk(job_id=uid, chunk_index=chunk_index, storage_key=chunk_key, status="queued")
                        job_repo.add_chunk(jc)
                        process_chunk.delay(job_id, chunk_index, chunk_key)
                        chunk_index += 1
            except Exception as e:
                logger.warning(f"Polars batched read failed: {e}, using pandas")
                use_polars = False

        if not use_polars:
            import pandas as pd
            for chunk_df in pd.read_csv(BytesIO(content), chunksize=CHUNK_LINES, encoding="utf-8", on_bad_lines="skip"):
                if chunk_df.empty:
                    continue
                chunk_bytes = chunk_df.to_csv(index=False).encode("utf-8")
                chunk_key = f"jobs/{job_id}/chunks/{chunk_index}.csv"
                if not upload_file_obj(bucket, chunk_key, BytesIO(chunk_bytes), content_type="text/csv"):
                    break
                jc = JobChunk(job_id=uid, chunk_index=chunk_index, storage_key=chunk_key, status="queued")
                job_repo.add_chunk(jc)
                process_chunk.delay(job_id, chunk_index, chunk_key)
                chunk_index += 1

        job.total_chunks = chunk_index
        job.status = "processing"
        if chunk_index == 0:
            from app.repositories.dataset_repository import DatasetRepository
            ds_repo = DatasetRepository(db)
            dataset = ds_repo.get_by_id(job.dataset_id, job.user_id)
            if dataset:
                dataset.status = "completed"
                dataset.row_count = 0
        db.commit()
        logger.info(
            "split_and_enqueue_chunks done",
            extra={"job_id": job_id, "total_chunks": chunk_index},
        )
    except Exception as exc:
        logger.exception(f"split_and_enqueue_chunks failed for job {job_id}: {exc}")
        try:
            job_repo = JobRepository(SessionLocal())
            job = job_repo.get_by_id(UUID(job_id))
            if job:
                job.status = "error"
                job_repo.db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()


@celery_app.task(bind=True, acks_late=True, max_retries=3, soft_time_limit=1100, time_limit=1200)
def process_chunk(self, job_id: str, chunk_index: int, storage_key: str):
    """
    Download chunk from storage, validate and aggregate (Polars first, pandas fallback),
    bulk_create rows, update job_chunk status and job chunks_done; on last chunk update dataset.
    """
    from app.db.session import SessionLocal
    from app.repositories.job_repository import JobRepository
    from app.repositories.dataset_repository import DatasetRepository
    from app.models.dataset_row import DatasetRow
    from app.models.click_row import ClickRow
    from app.services.storage import download_file
    from app.core.config import settings
    from sqlalchemy import func

    db = SessionLocal()
    try:
        uid = UUID(job_id)
        job_repo = JobRepository(db)
        job = job_repo.get_by_id(uid)
        if not job:
            logger.warning(f"process_chunk: job {job_id} not found")
            return

        content = download_file(settings.S3_BUCKET, storage_key)
        if not content:
            job_repo.set_chunk_status(uid, chunk_index, "failed", "Download failed")
            job_repo.db.commit()
            return

        t0 = time.monotonic()
        if job.type == "transaction":
            from app.services.csv_polars import process_transaction_chunk
            inserted = process_transaction_chunk(db, job.dataset_id, job.user_id, content)
        else:
            from app.services.csv_polars import process_click_chunk
            inserted = process_click_chunk(db, job.dataset_id, job.user_id, content)
        duration_s = round(time.monotonic() - t0, 2)
        logger.info(
            "process_chunk done",
            extra={
                "job_id": job_id,
                "chunk_index": chunk_index,
                "rows_processed": inserted,
                "duration_seconds": duration_s,
            },
        )

        job_repo.set_chunk_status(uid, chunk_index, "done", None)
        new_done = job_repo.update_job_chunks_done(uid, 1)
        job_repo.db.commit()

        if new_done is not None and job.total_chunks and new_done >= job.total_chunks:
            dataset_repo = DatasetRepository(db)
            dataset = dataset_repo.get_by_id(job.dataset_id, job.user_id)
            if dataset:
                if job.type == "transaction":
                    count = db.query(func.count(DatasetRow.id)).filter(DatasetRow.dataset_id == job.dataset_id).scalar()
                else:
                    count = db.query(func.count(ClickRow.id)).filter(ClickRow.dataset_id == job.dataset_id).scalar()
                dataset.row_count = count or 0
                dataset.status = "completed"
                db.commit()
    except Exception as exc:
        logger.exception(f"process_chunk failed job {job_id} chunk {chunk_index}: {exc}")
        try:
            job_repo.set_chunk_status(UUID(job_id), chunk_index, "failed", str(exc))
            job_repo.db.commit()
        except Exception:
            pass
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
